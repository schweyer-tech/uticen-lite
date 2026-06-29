# Control Plane — Local Web App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pip-installable, SQLite-backed local web app ("control plane") to the Uticen SDK that replaces hand-edited YAML/Python authoring with forms + a no-code rule builder (Python escape hatch), reusing the existing run/render/bundle core.

**Architecture:** Three new layers over the unchanged core: `store/` (SQLite source of truth + versioned migrator + repo), `rules/` (rule_spec → violation dicts via vectorized pandas), and `plane/` (FastAPI + Jinja + HTMX web app). The existing `ControlDef`/runner are extended to carry inline test code and a rule spec and to branch execution accordingly; everything downstream (`Workpaper.assemble`, `render_html`, `assemble_bundle`, `write_bundle`) is reused. An engagement is one folder with `controlplane.db`, `data/`, and `target/`.

**Tech Stack:** Python ≥3.11, pandas, sqlite3 (stdlib), FastAPI + uvicorn + Jinja2 + python-multipart, HTMX + vendored CodeMirror (offline), jsonschema, pytest/ruff/mypy, hatchling.

## Global Constraints

- Python floor: `requires-python = ">=3.11"`; ruff `target-version = "py311"`, `line-length = 100`, `select = ["E","F","I","UP"]`. Keep `ruff check .` and `mypy uticen_lite` green every task. No `Any` leaks in new public signatures where avoidable.
- SQLite is the single source of truth; the web app is the primary surface. **Brittle-by-design:** trust the folder convention (`controlplane.db`, `data/`, `target/`), do only light validation. Robustness is paid Uticen's job.
- The web app binds `127.0.0.1` only, no auth, **zero network egress**; CodeMirror is vendored (no CDN). No Node, no build step.
- The export bundle MUST validate against `contract/bundle.schema.json` (unchanged) — the control plane is a new *producer* of the existing bundle shape. Required control keys: `id, title, objective, narrative, framework_refs, sources, test_code, workpaper, runs`. Required run keys: `run_id, executed_at, passed, failed, total, pass_rate, summary, details, control_id, provenance`.
- Scope stops at author → run → view → export. No local exception disposition/sign-off, no multi-source rule builder, no Pyodide sandbox, no network connectors.
- Reuse, don't fork: `Workpaper.assemble`, `render_html`, `render_markdown`, `collect_data_samples`, `assemble_bundle`, `write_bundle`, the `adapters/files` loaders, and `Violation.from_raw`.

## EXECUTION RULES (read first)

- **Never ask the user for permission to continue between tasks.** Execute the full plan start to finish without interruption.
- On an unresolvable error after 2–3 attempts: note it inline in your progress report and skip to the next task.
- **After every `git commit`, push:**
  ```bash
  git push -u origin HEAD
  ```
  (The SDK repo is `main`-gated only by CI; this plan's work lands on branch `feat/control-plane-local-web-app`, already pushed. No extra post-push CI command for this repo.)
- TDD throughout: failing test → run it red → minimal code → run it green → commit. Run the full `pytest` before the commit of any task that touches shared core (`model/`, `runner/`, `bundle/`).

## File Structure (what each new/changed file owns)

**New — `uticen_lite/store/`**
- `db.py` — open a connection to `controlplane.db`, apply pragmas, expose `connect(project_root) -> sqlite3.Connection`.
- `migrations.py` — ordered DDL steps + `migrate(conn)`; `SCHEMA_VERSION` constant.
- `repo.py` — typed CRUD over the tables (project, sources, columns, controls, control_sources, runs, violations). One module; functions, not classes.
- `loader.py` — `load_project_from_store(conn, root) -> Project` (builds `ProjectConfig` + `dict[str, SourceBinding]` + `list[ControlDef]`).
- `run_service.py` — `run_control_in_store(conn, root, control_id, executed_at) -> RunRecord` (run + persist + render workpaper to `target/`).

**New — `uticen_lite/rules/`**
- `spec.py` — `RuleSpec` dataclass + `parse_rule_spec(raw: dict) -> RuleSpec` (validates operators/logic; raises `RuleSpecError`).
- `evaluate.py` — `evaluate_rule(spec: RuleSpec, pop: Population) -> list[dict]` (vectorized).
- `render_rule.py` — `rule_to_text(spec: RuleSpec) -> str` (human-readable "test that ran").

**New — `uticen_lite/plane/`**
- `__main__.py` — `main(argv=None) -> int`: arg parse, bootstrap folder/db, launch uvicorn, open browser.
- `app.py` — `create_app(project_root: Path) -> FastAPI`: routes, templates, static mount, per-request db.
- `routes/` — `dashboard.py`, `sources.py`, `controls.py`, `runs.py`, `export.py` (route functions registered by `app.py`).
- `templates/` — Jinja2 (`base.html`, `dashboard.html`, `sources.html`, `source_edit.html`, `control_edit.html`, `run_view.html`, partials under `templates/partials/`).
- `static/` — vendored CodeMirror (`codemirror.min.js`, `codemirror.min.css`, python mode) + `app.css` + `htmx.min.js`.

**New — `uticen_lite/cli/import_cmd.py`** — `import_cmd(args) -> int` (YAML project → store).

**Modified core**
- `model/control.py` — extend `ControlDef`: add `test_code: str | None = None`, `rule_spec: dict[str, Any] | None = None`; make `test_path: str = ""`; add `test_kind` property; update `to_dict`.
- `runner/execute.py` — `run_control` branches on `control.rule_spec`; `load_test_callable` accepts inline `test_code`.
- `bundle/assemble.py` — resolve `test_code` from `control.test_code`/`rule_to_text` when `test_path` is empty.
- `cli/__init__.py` — register `import`; route `run`/`build` through the store loader; drop `init`/`new` from dispatch.
- `pyproject.toml` — `[project.optional-dependencies] plane`, `[project.scripts] controlplane`, hatch force-include of `plane/static`, `plane/templates`.

---

## Phase 1 — Store (SQLite source of truth)

### Task 1: SQLite connection + versioned migrator

**Files:**
- Create: `uticen_lite/store/__init__.py` (empty), `uticen_lite/store/db.py`, `uticen_lite/store/migrations.py`
- Test: `tests/store/__init__.py` (empty), `tests/store/test_migrations.py`

**Interfaces:**
- Produces: `store.db.connect(project_root: Path) -> sqlite3.Connection`; `store.migrations.migrate(conn: sqlite3.Connection) -> None`; `store.migrations.SCHEMA_VERSION: int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_migrations.py
from pathlib import Path

from uticen_lite.store.db import connect
from uticen_lite.store.migrations import SCHEMA_VERSION, migrate


def _user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def test_migrate_creates_all_tables_and_sets_version(tmp_path: Path):
    conn = connect(tmp_path)
    migrate(conn)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "project", "sources", "columns", "controls",
        "control_sources", "runs", "violations",
    } <= tables
    assert _user_version(conn) == SCHEMA_VERSION


def test_migrate_is_idempotent(tmp_path: Path):
    conn = connect(tmp_path)
    migrate(conn)
    migrate(conn)  # second run must be a no-op, not raise
    assert _user_version(conn) == SCHEMA_VERSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/store/test_migrations.py -v`
Expected: FAIL with `ModuleNotFoundError: uticen_lite.store`.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/store/db.py
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_FILENAME = "controlplane.db"


def connect(project_root: Path) -> sqlite3.Connection:
    """Open (creating if needed) the engagement DB under project_root."""
    project_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(project_root / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```

```python
# uticen_lite/store/migrations.py
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

# Forward-only, idempotent DDL. Index = target user_version.
_STEPS: list[str] = [
    # --- step 1 -> user_version 1 -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS project (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        name          TEXT NOT NULL DEFAULT '',
        framework     TEXT,
        system        TEXT,           -- JSON
        created_at    TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS sources (
        id            TEXT PRIMARY KEY,
        format        TEXT NOT NULL,  -- csv | parquet | xlsx
        path          TEXT NOT NULL,  -- relative, under data/
        key_config    TEXT NOT NULL DEFAULT '{}',  -- JSON
        description   TEXT,
        completeness_accuracy TEXT,
        extract_date  TEXT,
        created_at    TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS columns (
        source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        original_name TEXT NOT NULL,
        display_name  TEXT NOT NULL,
        data_type     TEXT NOT NULL DEFAULT 'text',
        is_key        INTEGER NOT NULL DEFAULT 0,
        include       INTEGER NOT NULL DEFAULT 1,
        ordinal       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (source_id, original_name)
    );
    CREATE TABLE IF NOT EXISTS controls (
        id            TEXT PRIMARY KEY,
        title         TEXT NOT NULL DEFAULT '',
        objective     TEXT NOT NULL DEFAULT '',
        narrative     TEXT NOT NULL DEFAULT '',
        framework_refs TEXT NOT NULL DEFAULT '{}',  -- JSON {nist:[...], extra:{...}}
        failure_threshold_pct   REAL,
        failure_threshold_count INTEGER,
        test_kind     TEXT NOT NULL DEFAULT 'rule',  -- rule | python
        rule_spec     TEXT,            -- JSON when test_kind=rule
        test_code     TEXT,            -- text when test_kind=python
        created_at    TEXT NOT NULL DEFAULT '',
        updated_at    TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS control_sources (
        control_id    TEXT NOT NULL REFERENCES controls(id) ON DELETE CASCADE,
        source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        ordinal       INTEGER NOT NULL DEFAULT 0,  -- 0 = primary
        PRIMARY KEY (control_id, source_id)
    );
    CREATE TABLE IF NOT EXISTS runs (
        run_id          TEXT PRIMARY KEY,
        control_id      TEXT NOT NULL REFERENCES controls(id) ON DELETE CASCADE,
        executed_at     TEXT NOT NULL,
        population_size INTEGER NOT NULL DEFAULT 0,
        total           INTEGER NOT NULL DEFAULT 0,
        passed          INTEGER NOT NULL DEFAULT 0,
        failed          INTEGER NOT NULL DEFAULT 0,
        pass_rate       REAL NOT NULL DEFAULT 0,
        provenance      TEXT NOT NULL DEFAULT '[]',  -- JSON list[SourceProvenance.to_dict()]
        created_at      TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS violations (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        item_key      TEXT NOT NULL,
        description   TEXT NOT NULL,
        severity      TEXT NOT NULL DEFAULT 'medium',
        details       TEXT NOT NULL DEFAULT '{}'  -- JSON
    );
    """,
]


def migrate(conn: sqlite3.Connection) -> None:
    """Apply all forward steps beyond the DB's current user_version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for idx, ddl in enumerate(_STEPS, start=1):
        if idx <= current:
            continue
        conn.executescript(ddl)
        conn.execute(f"PRAGMA user_version = {idx}")
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/store/test_migrations.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/store tests/store
git commit -m "feat(store): sqlite connection + versioned migrator"
git push -u origin HEAD
```

### Task 2: Extend `ControlDef` with inline code + rule spec

**Files:**
- Modify: `uticen_lite/model/control.py` (`ControlDef` dataclass + `to_dict`)
- Test: `tests/model/test_control.py` (append cases)

**Interfaces:**
- Produces: `ControlDef(... , test_path: str = "", test_code: str | None = None, rule_spec: dict[str, Any] | None = None)` with property `test_kind -> str` returning `"rule"` if `rule_spec` else `"python"`. Back-compat: existing positional construction with `test_path` still works.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_control.py  (append)
from uticen_lite.model.control import ControlDef, FrameworkRefs, Threshold


def _base(**kw):
    defaults = dict(
        id="c1", title="t", objective="o", narrative="n",
        framework_refs=FrameworkRefs(), risk=None, sources=[],
    )
    defaults.update(kw)
    return ControlDef(**defaults)


def test_control_defaults_to_python_kind_with_test_path():
    c = _base(test_path="controls/c1/test.py")
    assert c.test_kind == "python"
    assert c.test_code is None and c.rule_spec is None


def test_control_with_rule_spec_is_rule_kind():
    spec = {"logic": "all", "conditions": [], "severity": "high"}
    c = _base(rule_spec=spec)
    assert c.test_kind == "rule"
    assert c.to_dict()["test_kind"] == "rule"


def test_control_with_inline_code_is_python_kind():
    c = _base(test_code="def test(pop):\n    return []")
    assert c.test_kind == "python"
    assert "test_code" in c.to_dict()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_control.py -k "rule_spec or inline_code or defaults_to_python" -v`
Expected: FAIL — `ControlDef.__init__() got an unexpected keyword argument 'test_code'`.

- [ ] **Step 3: Write minimal implementation**

In `uticen_lite/model/control.py`, change the `ControlDef` dataclass fields and `to_dict` (keep all existing fields; add the three new ones with defaults; make `test_path` default to `""`):

```python
@dataclass
class ControlDef:
    id: str
    title: str
    objective: str
    narrative: str
    framework_refs: FrameworkRefs
    risk: RiskRef | None
    sources: list[SourceBinding]
    test_path: str = ""
    test_code: str | None = None
    rule_spec: dict[str, Any] | None = None
    severity_policy: dict[str, Any] = field(default_factory=dict)
    threshold: Threshold = field(default_factory=Threshold)

    @property
    def test_kind(self) -> str:
        return "rule" if self.rule_spec is not None else "python"

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "narrative": self.narrative,
            "framework_refs": self.framework_refs.to_dict()
            if hasattr(self.framework_refs, "to_dict")
            else self.framework_refs,
            "risk": self.risk.__dict__ if self.risk else None,
            "sources": [s.to_data_source() for s in self.sources],
            "test_path": self.test_path,
            "test_kind": self.test_kind,
            "threshold": self.threshold.to_dict(),
        }
        if self.test_code is not None:
            data["test_code"] = self.test_code
        if self.rule_spec is not None:
            data["rule_spec"] = self.rule_spec
        return data
```

> If the existing `to_dict` differs, preserve its existing keys and just add `test_kind`, and the conditional `test_code`/`rule_spec`. Do not remove keys other tasks/tests rely on.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/model/test_control.py -v`
Expected: PASS (new + all existing control tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/model/control.py tests/model/test_control.py
git commit -m "feat(model): ControlDef carries inline test_code + rule_spec"
git push -u origin HEAD
```

### Task 3: Repo CRUD — project, sources, columns

**Files:**
- Create: `uticen_lite/store/repo.py`
- Test: `tests/store/test_repo_sources.py`

**Interfaces:**
- Produces (in `store.repo`):
  - `upsert_project(conn, *, name, framework=None, system=None, created_at="") -> None`
  - `get_project(conn) -> dict | None`
  - `upsert_source(conn, *, id, format, path, key_config: dict, description=None, completeness_accuracy=None, extract_date=None, created_at="") -> None`
  - `set_columns(conn, source_id: str, columns: list[dict]) -> None` (replaces all columns for the source; each dict: `original_name, display_name, data_type, is_key, include, ordinal`)
  - `list_sources(conn) -> list[dict]`; `get_source(conn, source_id) -> dict | None` (with `"columns": [...]`)

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_repo_sources.py
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    return conn


def test_upsert_and_get_project(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_project(conn, name="Acme", framework="nist", system={"name": "GSS"})
    p = repo.get_project(conn)
    assert p["name"] == "Acme"
    assert p["framework"] == "nist"
    assert p["system"] == {"name": "GSS"}  # JSON-decoded


def test_source_with_columns_roundtrip(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(
        conn, id="users", format="csv", path="data/users.csv",
        key_config={"mode": "single", "columns": ["user_id"]},
    )
    repo.set_columns(conn, "users", [
        {"original_name": "user_id", "display_name": "User ID",
         "data_type": "text", "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "can_create", "display_name": "Can Create",
         "data_type": "boolean", "is_key": False, "include": True, "ordinal": 1},
    ])
    src = repo.get_source(conn, "users")
    assert src["format"] == "csv"
    assert src["key_config"] == {"mode": "single", "columns": ["user_id"]}
    assert [c["original_name"] for c in src["columns"]] == ["user_id", "can_create"]
    assert src["columns"][0]["is_key"] is True


def test_set_columns_replaces(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(conn, id="s", format="csv", path="data/s.csv", key_config={})
    repo.set_columns(conn, "s", [{"original_name": "a", "display_name": "A",
        "data_type": "text", "is_key": False, "include": True, "ordinal": 0}])
    repo.set_columns(conn, "s", [{"original_name": "b", "display_name": "B",
        "data_type": "text", "is_key": False, "include": True, "ordinal": 0}])
    assert [c["original_name"] for c in repo.get_source(conn, "s")["columns"]] == ["b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/store/test_repo_sources.py -v`
Expected: FAIL — `ImportError: cannot import name 'repo'` / `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/store/repo.py
from __future__ import annotations

import json
import sqlite3
from typing import Any


def _loads(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


# ---- project ---------------------------------------------------------------
def upsert_project(
    conn: sqlite3.Connection, *, name: str, framework: str | None = None,
    system: dict | None = None, created_at: str = "",
) -> None:
    conn.execute(
        """INSERT INTO project (id, name, framework, system, created_at)
           VALUES (1, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, framework=excluded.framework,
             system=excluded.system""",
        (name, framework, json.dumps(system) if system is not None else None, created_at),
    )
    conn.commit()


def get_project(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM project WHERE id = 1").fetchone()
    if row is None:
        return None
    d = dict(row)
    d["system"] = _loads(d.get("system"), {})
    return d


# ---- sources + columns -----------------------------------------------------
def upsert_source(
    conn: sqlite3.Connection, *, id: str, format: str, path: str,
    key_config: dict, description: str | None = None,
    completeness_accuracy: str | None = None, extract_date: str | None = None,
    created_at: str = "",
) -> None:
    conn.execute(
        """INSERT INTO sources
             (id, format, path, key_config, description,
              completeness_accuracy, extract_date, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             format=excluded.format, path=excluded.path,
             key_config=excluded.key_config, description=excluded.description,
             completeness_accuracy=excluded.completeness_accuracy,
             extract_date=excluded.extract_date""",
        (id, format, path, json.dumps(key_config), description,
         completeness_accuracy, extract_date, created_at),
    )
    conn.commit()


def set_columns(conn: sqlite3.Connection, source_id: str, columns: list[dict]) -> None:
    conn.execute("DELETE FROM columns WHERE source_id = ?", (source_id,))
    conn.executemany(
        """INSERT INTO columns
             (source_id, original_name, display_name, data_type,
              is_key, include, ordinal)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (source_id, c["original_name"], c["display_name"], c.get("data_type", "text"),
             int(bool(c.get("is_key"))), int(bool(c.get("include", True))),
             int(c.get("ordinal", i)))
            for i, c in enumerate(columns)
        ],
    )
    conn.commit()


def _columns_for(conn: sqlite3.Connection, source_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM columns WHERE source_id = ? ORDER BY ordinal", (source_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_key"] = bool(d["is_key"])
        d["include"] = bool(d["include"])
        out.append(d)
    return out


def get_source(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["key_config"] = _loads(d.get("key_config"), {})
    d["columns"] = _columns_for(conn, source_id)
    return d


def list_sources(conn: sqlite3.Connection) -> list[dict]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM sources ORDER BY id").fetchall()]
    return [get_source(conn, sid) for sid in ids]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/store/test_repo_sources.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/store/repo.py tests/store/test_repo_sources.py
git commit -m "feat(store): repo CRUD for project, sources, columns"
git push -u origin HEAD
```

### Task 4: Repo CRUD — controls + bindings

**Files:**
- Modify: `uticen_lite/store/repo.py` (append control functions)
- Test: `tests/store/test_repo_controls.py`

**Interfaces:**
- Produces:
  - `upsert_control(conn, *, id, title, objective, narrative, framework_refs: dict, failure_threshold_pct=None, failure_threshold_count=None, test_kind, rule_spec: dict|None=None, test_code: str|None=None, created_at="", updated_at="") -> None`
  - `set_control_sources(conn, control_id: str, source_ids: list[str]) -> None` (ordinal = list index; index 0 = primary)
  - `get_control(conn, control_id) -> dict | None` (includes `"source_ids": [...]` ordered by ordinal, `rule_spec`/`framework_refs` JSON-decoded)
  - `list_controls(conn) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_repo_controls.py
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_source(conn, id="users", format="csv", path="data/users.csv", key_config={})
    return conn


def test_rule_control_roundtrip(tmp_path):
    conn = _db(tmp_path)
    spec = {"logic": "all", "conditions": [{"column": "x", "op": "eq", "value": 1}],
            "severity": "high"}
    repo.upsert_control(
        conn, id="c1", title="SoD", objective="o", narrative="n",
        framework_refs={"nist": ["AC-5"]}, test_kind="rule", rule_spec=spec,
        failure_threshold_count=0,
    )
    repo.set_control_sources(conn, "c1", ["users"])
    c = repo.get_control(conn, "c1")
    assert c["test_kind"] == "rule"
    assert c["rule_spec"] == spec
    assert c["framework_refs"] == {"nist": ["AC-5"]}
    assert c["source_ids"] == ["users"]
    assert c["failure_threshold_count"] == 0


def test_python_control_roundtrip(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_control(
        conn, id="c2", title="t", objective="o", narrative="n",
        framework_refs={}, test_kind="python",
        test_code="def test(pop):\n    return []",
    )
    c = repo.get_control(conn, "c2")
    assert c["test_kind"] == "python"
    assert c["test_code"].startswith("def test(pop)")
    assert c["rule_spec"] is None


def test_set_control_sources_orders_by_index(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(conn, id="b", format="csv", path="data/b.csv", key_config={})
    repo.upsert_control(conn, id="c3", title="t", objective="o", narrative="n",
                        framework_refs={}, test_kind="python", test_code="x")
    repo.set_control_sources(conn, "c3", ["b", "users"])
    assert repo.get_control(conn, "c3")["source_ids"] == ["b", "users"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/store/test_repo_controls.py -v`
Expected: FAIL — `AttributeError: module 'uticen_lite.store.repo' has no attribute 'upsert_control'`.

- [ ] **Step 3: Write minimal implementation** (append to `store/repo.py`)

```python
def upsert_control(
    conn: sqlite3.Connection, *, id: str, title: str, objective: str, narrative: str,
    framework_refs: dict, test_kind: str, rule_spec: dict | None = None,
    test_code: str | None = None, failure_threshold_pct: float | None = None,
    failure_threshold_count: int | None = None, created_at: str = "", updated_at: str = "",
) -> None:
    conn.execute(
        """INSERT INTO controls
             (id, title, objective, narrative, framework_refs,
              failure_threshold_pct, failure_threshold_count,
              test_kind, rule_spec, test_code, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             title=excluded.title, objective=excluded.objective,
             narrative=excluded.narrative, framework_refs=excluded.framework_refs,
             failure_threshold_pct=excluded.failure_threshold_pct,
             failure_threshold_count=excluded.failure_threshold_count,
             test_kind=excluded.test_kind, rule_spec=excluded.rule_spec,
             test_code=excluded.test_code, updated_at=excluded.updated_at""",
        (id, title, objective, narrative, json.dumps(framework_refs),
         failure_threshold_pct, failure_threshold_count, test_kind,
         json.dumps(rule_spec) if rule_spec is not None else None,
         test_code, created_at, updated_at),
    )
    conn.commit()


def set_control_sources(conn: sqlite3.Connection, control_id: str, source_ids: list[str]) -> None:
    conn.execute("DELETE FROM control_sources WHERE control_id = ?", (control_id,))
    conn.executemany(
        "INSERT INTO control_sources (control_id, source_id, ordinal) VALUES (?, ?, ?)",
        [(control_id, sid, i) for i, sid in enumerate(source_ids)],
    )
    conn.commit()


def _source_ids_for(conn: sqlite3.Connection, control_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT source_id FROM control_sources WHERE control_id = ? ORDER BY ordinal",
        (control_id,),
    ).fetchall()
    return [r["source_id"] for r in rows]


def get_control(conn: sqlite3.Connection, control_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM controls WHERE id = ?", (control_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["framework_refs"] = _loads(d.get("framework_refs"), {})
    d["rule_spec"] = _loads(d.get("rule_spec"), None)
    d["source_ids"] = _source_ids_for(conn, control_id)
    return d


def list_controls(conn: sqlite3.Connection) -> list[dict]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM controls ORDER BY id").fetchall()]
    return [get_control(conn, cid) for cid in ids]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/store/test_repo_controls.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/store/repo.py tests/store/test_repo_controls.py
git commit -m "feat(store): repo CRUD for controls + source bindings"
git push -u origin HEAD
```

### Task 5: Repo CRUD — runs + violations

**Files:**
- Modify: `uticen_lite/store/repo.py` (append run functions)
- Test: `tests/store/test_repo_runs.py`

**Interfaces:**
- Produces:
  - `insert_run(conn, run: RunRecord) -> None` — persists a `runs` row from `RunRecord` (run_id, control_id, executed_at, population_size, total/passed/failed/pass_rate, provenance JSON) + its `violations`.
  - `list_runs_for(conn, control_id) -> list[dict]` (newest first by executed_at)
  - `latest_run(conn, control_id) -> dict | None`
  - `get_run(conn, run_id) -> dict | None` (includes `"violations": [...]`)

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_repo_runs.py
from uticen_lite.model.run import RunRecord, SourceProvenance
from uticen_lite.model.violation import Severity, Violation
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_control(conn, id="c1", title="t", objective="o", narrative="n",
                        framework_refs={}, test_kind="python", test_code="x")
    return conn


def _run():
    return RunRecord(
        control_id="c1", executed_at="2026-03-31T00:00:00+00:00", population_size=3,
        violations=[Violation(item_key="U1", description="bad", severity=Severity.HIGH,
                              details={"amount": 5})],
        provenance=[SourceProvenance(source_id="users", path="data/users.csv",
                                     sha256="abc", row_count=3)],
    )


def test_insert_and_get_run(tmp_path):
    conn = _db(tmp_path)
    run = _run()
    repo.insert_run(conn, run)
    got = repo.get_run(conn, run.run_id)
    assert got["control_id"] == "c1"
    assert got["failed"] == 1 and got["total"] == 3
    assert got["violations"][0]["item_key"] == "U1"
    assert got["violations"][0]["details"] == {"amount": 5}
    assert got["provenance"][0]["sha256"] == "abc"


def test_latest_run(tmp_path):
    conn = _db(tmp_path)
    older = _run()
    newer = RunRecord(control_id="c1", executed_at="2026-04-01T00:00:00+00:00",
                      population_size=3, violations=[], provenance=[])
    repo.insert_run(conn, older)
    repo.insert_run(conn, newer)
    assert repo.latest_run(conn, "c1")["run_id"] == newer.run_id
    assert len(repo.list_runs_for(conn, "c1")) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/store/test_repo_runs.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'insert_run'`.

- [ ] **Step 3: Write minimal implementation** (append to `store/repo.py`; add `from uticen_lite.model.run import RunRecord` at top)

```python
def insert_run(conn: sqlite3.Connection, run: "RunRecord") -> None:
    conn.execute(
        """INSERT OR REPLACE INTO runs
             (run_id, control_id, executed_at, population_size,
              total, passed, failed, pass_rate, provenance, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run.run_id, run.control_id, run.executed_at, run.population_size,
         run.population_size, run.passed, run.failed, run.pass_rate,
         json.dumps([p.to_dict() for p in run.provenance]), run.executed_at),
    )
    conn.execute("DELETE FROM violations WHERE run_id = ?", (run.run_id,))
    conn.executemany(
        """INSERT INTO violations (run_id, item_key, description, severity, details)
           VALUES (?, ?, ?, ?, ?)""",
        [(run.run_id, v.item_key, v.description, str(v.severity), json.dumps(v.details))
         for v in run.violations],
    )
    conn.commit()


def _violations_for(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT item_key, description, severity, details FROM violations "
        "WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["details"] = _loads(d.get("details"), {})
        out.append(d)
    return out


def get_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["provenance"] = _loads(d.get("provenance"), [])
    d["violations"] = _violations_for(conn, run_id)
    return d


def list_runs_for(conn: sqlite3.Connection, control_id: str) -> list[dict]:
    ids = [
        r["run_id"]
        for r in conn.execute(
            "SELECT run_id FROM runs WHERE control_id = ? "
            "ORDER BY executed_at DESC, created_at DESC", (control_id,)
        ).fetchall()
    ]
    return [get_run(conn, rid) for rid in ids]


def latest_run(conn: sqlite3.Connection, control_id: str) -> dict | None:
    runs = list_runs_for(conn, control_id)
    return runs[0] if runs else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/store/test_repo_runs.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/store/repo.py tests/store/test_repo_runs.py
git commit -m "feat(store): repo CRUD for runs + violations"
git push -u origin HEAD
```

### Task 6: Store loader → `Project`

**Files:**
- Create: `uticen_lite/store/loader.py`
- Test: `tests/store/test_store_loader.py`

**Interfaces:**
- Consumes: `repo.*`, `model.control.{ControlDef, FrameworkRefs, RiskRef, SourceBinding, Threshold}`, `project.discovery.Project`, `project.loader.ProjectConfig`.
- Produces: `load_project_from_store(conn: sqlite3.Connection) -> Project`. Each store source → `SourceBinding(id, type="file", config={"path", "format"}, key_config, column_mappings=[{original_name, display_name, data_type, is_key, include}, ...], description, completeness_accuracy, extract_date)`. Each store control → `ControlDef(... test_path="", test_code=<code or None>, rule_spec=<spec or None>, threshold=Threshold(pct,count), sources=[bound SourceBinding in ordinal order])`.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_store_loader.py
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.loader import load_project_from_store
from uticen_lite.store.migrations import migrate


def _seed(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme", framework="nist")
    repo.upsert_source(conn, id="users", format="csv", path="data/users.csv",
                       key_config={"mode": "single", "columns": ["user_id"]})
    repo.set_columns(conn, "users", [
        {"original_name": "user_id", "display_name": "User ID", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
    ])
    repo.upsert_control(conn, id="c1", title="SoD", objective="o", narrative="n",
                        framework_refs={"nist": ["AC-5"]}, test_kind="rule",
                        rule_spec={"logic": "all", "conditions": [], "severity": "high"},
                        failure_threshold_count=0)
    repo.set_control_sources(conn, "c1", ["users"])
    return conn


def test_load_project_from_store(tmp_path):
    conn = _seed(tmp_path)
    project = load_project_from_store(conn)
    assert project.config.name == "Acme"
    assert "users" in project.sources
    binding = project.sources["users"]
    assert binding.type == "file"
    assert binding.config["format"] == "csv"
    assert binding.config["path"] == "data/users.csv"
    assert binding.column_mappings[0]["original_name"] == "user_id"
    [control] = project.controls
    assert control.id == "c1"
    assert control.test_kind == "rule"
    assert control.rule_spec["severity"] == "high"
    assert control.framework_refs.nist == ["AC-5"]
    assert control.threshold.failure_threshold_count == 0
    # bound sources resolve to the same SourceBinding objects, in order
    assert [s.id for s in control.sources] == ["users"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/store/test_store_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: uticen_lite.store.loader`.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/store/loader.py
from __future__ import annotations

import sqlite3

from uticen_lite.model.control import (
    ControlDef,
    FrameworkRefs,
    SourceBinding,
    Threshold,
)
from uticen_lite.project.discovery import Project
from uticen_lite.project.loader import ProjectConfig
from uticen_lite.store import repo


def _binding(src: dict) -> SourceBinding:
    return SourceBinding(
        id=src["id"],
        type="file",
        config={"path": src["path"], "format": src["format"]},
        key_config=src["key_config"],
        column_mappings=[
            {
                "original_name": c["original_name"],
                "display_name": c["display_name"],
                "data_type": c["data_type"],
                "is_key": c["is_key"],
                "include": c["include"],
            }
            for c in src["columns"]
        ],
        description=src.get("description"),
        completeness_accuracy=src.get("completeness_accuracy"),
        extract_date=src.get("extract_date"),
    )


def _framework_refs(raw: dict) -> FrameworkRefs:
    raw = raw or {}
    return FrameworkRefs(
        nist=list(raw.get("nist", [])),
        extra={k: list(v) for k, v in raw.items() if k != "nist"},
    )


def load_project_from_store(conn: sqlite3.Connection) -> Project:
    proj = repo.get_project(conn) or {"name": "", "framework": None, "system": {}}
    config = ProjectConfig(
        name=proj.get("name", ""),
        framework=proj.get("framework"),
        system=proj.get("system") or {},
    )
    bindings = {src["id"]: _binding(src) for src in repo.list_sources(conn)}

    controls: list[ControlDef] = []
    for c in repo.list_controls(conn):
        controls.append(
            ControlDef(
                id=c["id"],
                title=c["title"],
                objective=c["objective"],
                narrative=c["narrative"],
                framework_refs=_framework_refs(c["framework_refs"]),
                risk=None,
                sources=[bindings[sid] for sid in c["source_ids"] if sid in bindings],
                test_path="",
                test_code=c["test_code"],
                rule_spec=c["rule_spec"],
                threshold=Threshold(
                    failure_threshold_pct=c["failure_threshold_pct"],
                    failure_threshold_count=c["failure_threshold_count"],
                ),
            )
        )
    return Project(config=config, sources=bindings, controls=controls)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/store/test_store_loader.py -v`
Expected: PASS.

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/store/loader.py tests/store/test_store_loader.py
git commit -m "feat(store): build a Project from the SQLite store"
git push -u origin HEAD
```

---

## Phase 2 — Rule engine

### Task 7: `RuleSpec` + validation

**Files:**
- Create: `uticen_lite/rules/__init__.py` (empty), `uticen_lite/rules/spec.py`
- Test: `tests/rules/__init__.py` (empty), `tests/rules/test_spec.py`

**Interfaces:**
- Produces: `rules.spec.RuleSpecError(Exception)`; dataclasses `Condition(column: str, op: str, value: Any = None)` and `RuleSpec(logic: str, conditions: list[Condition], severity: str = "medium", description_template: str = "", item_key_column: str | None = None)`; `OPERATORS: frozenset[str]`; `parse_rule_spec(raw: dict) -> RuleSpec` (raises `RuleSpecError` on unknown op, bad logic, or non-list conditions). `referenced_columns(spec) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/test_spec.py
import pytest

from uticen_lite.rules.spec import (
    OPERATORS,
    RuleSpec,
    RuleSpecError,
    parse_rule_spec,
    referenced_columns,
)


def test_parse_minimal_rule():
    spec = parse_rule_spec({
        "logic": "all",
        "conditions": [{"column": "can_create", "op": "eq", "value": True}],
        "severity": "high",
        "description_template": "User {user_id} flagged",
        "item_key_column": "user_id",
    })
    assert isinstance(spec, RuleSpec)
    assert spec.logic == "all"
    assert spec.conditions[0].column == "can_create"
    assert spec.severity == "high"
    assert referenced_columns(spec) == ["can_create"]


def test_operators_cover_v1_set():
    assert OPERATORS == frozenset({
        "eq", "ne", "gt", "ge", "lt", "le",
        "is_empty", "not_empty", "in", "not_in", "regex", "is_duplicate",
    })


def test_unknown_operator_raises():
    with pytest.raises(RuleSpecError):
        parse_rule_spec({"logic": "all",
                         "conditions": [{"column": "x", "op": "between", "value": 1}]})


def test_bad_logic_raises():
    with pytest.raises(RuleSpecError):
        parse_rule_spec({"logic": "xor", "conditions": []})


def test_conditions_must_be_list():
    with pytest.raises(RuleSpecError):
        parse_rule_spec({"logic": "all", "conditions": {"column": "x"}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/rules/test_spec.py -v`
Expected: FAIL — `ModuleNotFoundError: uticen_lite.rules`.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/rules/spec.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OPERATORS = frozenset({
    "eq", "ne", "gt", "ge", "lt", "le",
    "is_empty", "not_empty", "in", "not_in", "regex", "is_duplicate",
})
_LOGIC = frozenset({"all", "any"})


class RuleSpecError(ValueError):
    """A rule_spec is malformed."""


@dataclass(frozen=True)
class Condition:
    column: str
    op: str
    value: Any = None


@dataclass(frozen=True)
class RuleSpec:
    logic: str
    conditions: list[Condition] = field(default_factory=list)
    severity: str = "medium"
    description_template: str = ""
    item_key_column: str | None = None


def parse_rule_spec(raw: dict) -> RuleSpec:
    logic = raw.get("logic", "all")
    if logic not in _LOGIC:
        raise RuleSpecError(f"logic must be one of {sorted(_LOGIC)}, got {logic!r}")
    raw_conditions = raw.get("conditions", [])
    if not isinstance(raw_conditions, list):
        raise RuleSpecError("conditions must be a list")
    conditions = []
    for c in raw_conditions:
        op = c.get("op")
        if op not in OPERATORS:
            raise RuleSpecError(f"unknown operator {op!r}")
        if not c.get("column"):
            raise RuleSpecError("each condition needs a column")
        conditions.append(Condition(column=c["column"], op=op, value=c.get("value")))
    return RuleSpec(
        logic=logic,
        conditions=conditions,
        severity=raw.get("severity", "medium"),
        description_template=raw.get("description_template", ""),
        item_key_column=raw.get("item_key_column"),
    )


def referenced_columns(spec: RuleSpec) -> list[str]:
    seen: list[str] = []
    for c in spec.conditions:
        if c.column not in seen:
            seen.append(c.column)
    return seen
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/rules/test_spec.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/rules tests/rules/test_spec.py tests/rules/__init__.py
git commit -m "feat(rules): RuleSpec model + validation"
git push -u origin HEAD
```

### Task 8: Rule evaluator

**Files:**
- Create: `uticen_lite/rules/evaluate.py`
- Test: `tests/rules/test_evaluate.py`

**Interfaces:**
- Consumes: `rules.spec.{RuleSpec, Condition, referenced_columns}`, `model.population.Population`.
- Produces: `evaluate_rule(spec: RuleSpec, pop: Population) -> list[dict]` returning violation dicts `{item_key, description, severity, details}`. `item_key` from `spec.item_key_column` or, if unset, the population's single key column (`pop.key_columns[0]`), else the row index as string. `description` from `safe_format(template, row)` (unknown placeholders left literal). `details` = `{col: row[col]}` for `referenced_columns(spec)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/test_evaluate.py
import pandas as pd

from uticen_lite.model.population import ColumnMeta, Population
from uticen_lite.rules.evaluate import evaluate_rule
from uticen_lite.rules.spec import parse_rule_spec


def _pop(df: pd.DataFrame, key="user_id") -> Population:
    cols = [ColumnMeta(original_name=c, display_name=c,
                       is_key=(c == key)) for c in df.columns]
    return Population(df=df, columns=cols, source_id="s")


def test_and_logic_two_conditions():
    df = pd.DataFrame({
        "user_id": ["U1", "U2", "U3"],
        "can_create": [True, True, False],
        "can_approve": [True, False, True],
    })
    spec = parse_rule_spec({
        "logic": "all",
        "conditions": [
            {"column": "can_create", "op": "eq", "value": True},
            {"column": "can_approve", "op": "eq", "value": True},
        ],
        "severity": "high",
        "description_template": "User {user_id} can create and approve",
        "item_key_column": "user_id",
    })
    out = evaluate_rule(spec, _pop(df))
    assert [v["item_key"] for v in out] == ["U1"]
    assert out[0]["description"] == "User U1 can create and approve"
    assert out[0]["severity"] == "high"
    assert out[0]["details"] == {"can_create": True, "can_approve": True}


def test_any_logic():
    df = pd.DataFrame({"user_id": ["U1", "U2"], "amt": [10, 0]})
    spec = parse_rule_spec({"logic": "any", "conditions": [
        {"column": "amt", "op": "gt", "value": 5}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1"]


def test_is_empty_and_not_empty():
    df = pd.DataFrame({"user_id": ["U1", "U2"], "approver": ["", "boss"]})
    empty = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "approver", "op": "is_empty"}]})
    assert [v["item_key"] for v in evaluate_rule(empty, _pop(df))] == ["U1"]


def test_in_set():
    df = pd.DataFrame({"user_id": ["U1", "U2", "U3"], "role": ["admin", "user", "root"]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "role", "op": "in", "value": ["admin", "root"]}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1", "U3"]


def test_regex():
    df = pd.DataFrame({"user_id": ["U1", "U2"], "email": ["a@x.com", "bad"]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "email", "op": "regex", "value": r"^[^@]+@[^@]+\.[^@]+$"}]})
    # regex flags MATCHES; to flag malformed, author negates — here it flags valid ones
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1"]


def test_is_duplicate():
    df = pd.DataFrame({"user_id": ["U1", "U2", "U3"], "ssn": ["1", "1", "2"]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "ssn", "op": "is_duplicate"}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1", "U2"]


def test_item_key_defaults_to_population_key():
    df = pd.DataFrame({"user_id": ["U9"], "flag": [True]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "flag", "op": "eq", "value": True}]})  # no item_key_column
    assert evaluate_rule(spec, _pop(df))[0]["item_key"] == "U9"


def test_unknown_template_placeholder_left_literal():
    df = pd.DataFrame({"user_id": ["U1"], "flag": [True]})
    spec = parse_rule_spec({"logic": "all",
        "conditions": [{"column": "flag", "op": "eq", "value": True}],
        "description_template": "User {user_id} has {missing}"})
    assert evaluate_rule(spec, _pop(df))[0]["description"] == "User U1 has {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/rules/test_evaluate.py -v`
Expected: FAIL — `ModuleNotFoundError: uticen_lite.rules.evaluate`.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/rules/evaluate.py
from __future__ import annotations

from typing import Any

import pandas as pd

from uticen_lite.model.population import Population
from uticen_lite.rules.spec import Condition, RuleSpec, referenced_columns


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _safe_format(template: str, row: dict) -> str:
    if not template:
        return ""
    return template.format_map(_SafeDict(row))


def _condition_mask(df: pd.DataFrame, cond: Condition) -> pd.Series:
    col = df[cond.column]
    op, value = cond.op, cond.value
    if op == "eq":
        return col == value
    if op == "ne":
        return col != value
    if op == "gt":
        return col > value
    if op == "ge":
        return col >= value
    if op == "lt":
        return col < value
    if op == "le":
        return col <= value
    if op == "is_empty":
        return col.isna() | (col.astype(str) == "")
    if op == "not_empty":
        return ~(col.isna() | (col.astype(str) == ""))
    if op == "in":
        return col.isin(value or [])
    if op == "not_in":
        return ~col.isin(value or [])
    if op == "regex":
        return col.astype(str).str.match(str(value)).fillna(False)
    if op == "is_duplicate":
        return col.duplicated(keep=False)
    raise ValueError(f"unhandled operator {op!r}")  # pragma: no cover (validated upstream)


def evaluate_rule(spec: RuleSpec, pop: Population) -> list[dict]:
    df = pop.df
    if not spec.conditions:
        return []
    masks = [_condition_mask(df, c) for c in spec.conditions]
    combined = masks[0]
    for m in masks[1:]:
        combined = (combined & m) if spec.logic == "all" else (combined | m)

    key_col = spec.item_key_column
    if not key_col:
        key_col = pop.key_columns[0] if pop.key_columns else None
    ref_cols = referenced_columns(spec)

    violations: list[dict] = []
    for idx, row in df[combined].iterrows():
        row_map = row.to_dict()
        item_key = str(row_map[key_col]) if key_col else str(idx)
        details: dict[str, Any] = {c: row_map[c] for c in ref_cols if c in row_map}
        violations.append({
            "item_key": item_key,
            "description": _safe_format(spec.description_template, row_map),
            "severity": spec.severity,
            "details": details,
        })
    return violations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/rules/test_evaluate.py -v`
Expected: PASS (8 tests).

> Note: `Violation.from_raw` coerces `details` later; numpy scalars from `row.to_dict()` are JSON-serialized at store/bundle time via `str`/default — if a mypy or json error surfaces on numpy types, cast in `details` with `row_map[c].item() if hasattr(...,'item') else row_map[c]`. Apply only if a test fails.

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/rules/evaluate.py tests/rules/test_evaluate.py
git commit -m "feat(rules): vectorized rule evaluator → violation dicts"
git push -u origin HEAD
```

### Task 9: Render a rule as readable test text

**Files:**
- Create: `uticen_lite/rules/render_rule.py`
- Test: `tests/rules/test_render_rule.py`

**Interfaces:**
- Consumes: `rules.spec.{RuleSpec, Condition}`.
- Produces: `rule_to_text(spec: RuleSpec) -> str` — a human-readable, deterministic description used as the workpaper/bundle "test that ran" for rule controls.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/test_render_rule.py
from uticen_lite.rules.render_rule import rule_to_text
from uticen_lite.rules.spec import parse_rule_spec


def test_rule_to_text_reads_as_a_rule():
    spec = parse_rule_spec({
        "logic": "all",
        "conditions": [
            {"column": "can_create", "op": "eq", "value": True},
            {"column": "can_approve", "op": "eq", "value": True},
        ],
        "severity": "high",
    })
    text = rule_to_text(spec)
    assert "Flag a record when ALL of the following are true:" in text
    assert "can_create = True" in text
    assert "can_approve = True" in text
    assert "severity: high" in text


def test_any_logic_and_unary_op_render():
    spec = parse_rule_spec({"logic": "any", "conditions": [
        {"column": "approver", "op": "is_empty"},
        {"column": "ssn", "op": "is_duplicate"}]})
    text = rule_to_text(spec)
    assert "ANY of the following" in text
    assert "approver is empty" in text
    assert "ssn is duplicated" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/rules/test_render_rule.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/rules/render_rule.py
from __future__ import annotations

from uticen_lite.rules.spec import Condition, RuleSpec

_BINARY = {
    "eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=",
}
_SET = {"in": "in", "not_in": "not in"}
_UNARY = {"is_empty": "is empty", "not_empty": "is not empty",
          "is_duplicate": "is duplicated"}


def _condition_text(c: Condition) -> str:
    if c.op in _BINARY:
        return f"{c.column} {_BINARY[c.op]} {c.value}"
    if c.op in _SET:
        return f"{c.column} {_SET[c.op]} {c.value}"
    if c.op == "regex":
        return f"{c.column} matches /{c.value}/"
    if c.op in _UNARY:
        return f"{c.column} {_UNARY[c.op]}"
    return f"{c.column} {c.op} {c.value}"  # pragma: no cover


def rule_to_text(spec: RuleSpec) -> str:
    joiner = "ALL" if spec.logic == "all" else "ANY"
    lines = [f"Flag a record when {joiner} of the following are true:"]
    lines += [f"  - {_condition_text(c)}" for c in spec.conditions]
    lines.append(f"severity: {spec.severity}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/rules/test_render_rule.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/rules/render_rule.py tests/rules/test_render_rule.py
git commit -m "feat(rules): render a rule_spec as readable test text"
git push -u origin HEAD
```

---

## Phase 3 — Runner branch + headless CLI + import + bundle

### Task 10: `load_test_callable` accepts inline code

**Files:**
- Modify: `uticen_lite/project/discovery.py` (`load_test_callable`)
- Test: `tests/project/test_discovery.py` (append)

**Interfaces:**
- Produces: `load_test_callable(control: ControlDef) -> Callable[..., list[Any]]` now prefers `control.test_code` (compiled in a fresh namespace) when present; falls back to importing `control.test_path` (unchanged behavior). Raises the existing `ProjectError` when neither yields a callable `test`.

- [ ] **Step 1: Write the failing test**

```python
# tests/project/test_discovery.py  (append)
from uticen_lite.model.control import ControlDef, FrameworkRefs
from uticen_lite.project.discovery import load_test_callable


def _control(**kw):
    base = dict(id="c", title="t", objective="o", narrative="n",
                framework_refs=FrameworkRefs(), risk=None, sources=[])
    base.update(kw)
    return ControlDef(**base)


def test_load_test_callable_from_inline_code():
    c = _control(test_code="def test(pop):\n    return [{'item_key': 'X', 'description': 'd'}]")
    fn = load_test_callable(c)
    assert callable(fn)
    assert fn(None) == [{"item_key": "X", "description": "d"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/project/test_discovery.py -k inline_code -v`
Expected: FAIL — `load_test_callable` tries to read an empty `test_path` and raises / returns wrong.

- [ ] **Step 3: Write minimal implementation**

In `uticen_lite/project/discovery.py`, at the top of `load_test_callable`, before the file-import path:

```python
def load_test_callable(control: ControlDef) -> Callable[..., list[Any]]:
    # Inline code (control-plane store) takes precedence over a file path.
    if getattr(control, "test_code", None):
        namespace: dict[str, Any] = {}
        try:
            exec(compile(control.test_code, f"<control:{control.id}>", "exec"), namespace)
        except SyntaxError as exc:  # reuse existing ProjectError type
            raise ProjectError(f"control {control.id}: test code has a syntax error: {exc}") from exc
        fn = namespace.get("test")
        if not callable(fn):
            raise ProjectError(f"control {control.id}: inline test code defines no callable 'test'")
        return fn  # type: ignore[no-any-return]
    # ... existing file-based import path unchanged below ...
```

(Keep the rest of the function as-is. Ensure `Any` is imported in that module — it is, via existing typing imports.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/project/test_discovery.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/project/discovery.py tests/project/test_discovery.py
git commit -m "feat(runner): load_test_callable supports inline test_code"
git push -u origin HEAD
```

### Task 11: `run_control` branches on rule vs python

**Files:**
- Modify: `uticen_lite/runner/execute.py` (`run_control`)
- Test: `tests/runner/test_execute.py` (append)

**Interfaces:**
- Produces: `run_control(control, sources, root, executed_at) -> RunRecord` unchanged signature; when `control.rule_spec is not None`, it builds violations via `rules.evaluate.evaluate_rule(parse_rule_spec(control.rule_spec), primary)` instead of calling a Python callable. Provenance, population_size, RunRecord assembly all unchanged. Python controls behave exactly as before.

- [ ] **Step 1: Write the failing test**

```python
# tests/runner/test_execute.py  (append)
from pathlib import Path

import pandas as pd

from uticen_lite.model.control import ControlDef, FrameworkRefs, SourceBinding
from uticen_lite.runner.execute import run_control


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "data"
    p.mkdir()
    pd.DataFrame({"user_id": ["U1", "U2"], "can_create": ["true", "true"],
                  "can_approve": ["true", "false"]}).to_csv(p / "users.csv", index=False)
    return tmp_path


def _users_binding() -> SourceBinding:
    return SourceBinding(
        id="users", type="file",
        config={"path": "data/users.csv", "format": "csv"},
        key_config={"mode": "single", "columns": ["user_id"]},
        column_mappings=[
            {"original_name": "user_id", "display_name": "User ID",
             "data_type": "text", "is_key": True, "include": True},
            {"original_name": "can_create", "display_name": "Can Create",
             "data_type": "boolean", "is_key": False, "include": True},
            {"original_name": "can_approve", "display_name": "Can Approve",
             "data_type": "boolean", "is_key": False, "include": True},
        ],
    )


def test_run_control_executes_a_rule(tmp_path: Path):
    root = _csv(tmp_path)
    binding = _users_binding()
    control = ControlDef(
        id="sod", title="SoD", objective="o", narrative="n",
        framework_refs=FrameworkRefs(), risk=None, sources=[binding],
        rule_spec={
            "logic": "all",
            "conditions": [
                {"column": "can_create", "op": "eq", "value": True},
                {"column": "can_approve", "op": "eq", "value": True},
            ],
            "severity": "high",
            "description_template": "User {user_id} can create and approve",
            "item_key_column": "user_id",
        },
    )
    run = run_control(control, {"users": binding}, root, "2026-03-31T00:00:00+00:00")
    assert run.population_size == 2
    assert run.failed == 1
    assert run.violations[0].item_key == "U1"
    assert run.provenance[0].source_id == "users"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/runner/test_execute.py -k executes_a_rule -v`
Expected: FAIL — `run_control` tries `load_test_callable` on a control with no `test_code`/`test_path` → `ProjectError`.

- [ ] **Step 3: Write minimal implementation**

In `uticen_lite/runner/execute.py`, locate the section that selects the primary population and calls the test callable (around lines 185–195). Replace the "load + dispatch" block with a branch:

```python
    # primary already selected as `primary`; sources_by_id already built.
    if control.rule_spec is not None:
        from uticen_lite.rules.evaluate import evaluate_rule
        from uticen_lite.rules.spec import parse_rule_spec

        raw_result: Any = evaluate_rule(parse_rule_spec(control.rule_spec), primary)
    else:
        test_fn = load_test_callable(control)
        if _accepts_sources(test_fn):
            raw_result = test_fn(primary, sources_by_id)
        else:
            raw_result = test_fn(primary)

    # ... existing validation (list check) + Violation.from_raw coercion unchanged ...
```

Keep everything else (the `isinstance(raw_result, list)` validation, `Violation.from_raw` loop, `RunRecord(...)` construction) exactly as it is.

- [ ] **Step 4: Run the full runner suite to verify pass + no regression**

Run: `pytest tests/runner/ -v`
Expected: PASS (new rule test + all existing python-callable tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/runner/execute.py tests/runner/test_execute.py
git commit -m "feat(runner): run_control executes a rule_spec or python test"
git push -u origin HEAD
```

### Task 12: Store-backed run service (run + persist + render)

**Files:**
- Create: `uticen_lite/store/run_service.py`
- Test: `tests/store/test_run_service.py`

**Interfaces:**
- Consumes: `store.loader.load_project_from_store`, `runner.execute.{run_control, collect_data_samples}`, `model.workpaper.Workpaper`, `render.html.render_html`, `render.markdown.render_markdown`, `store.repo.insert_run`.
- Produces: `run_control_in_store(conn, root: Path, control_id: str, executed_at: str) -> RunRecord` — loads the project from the store, runs the one control, persists the run + violations, writes `target/workpapers/<id>.{html,md}` and `target/evidence/<id>-violations.json`, returns the `RunRecord`.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_run_service.py
from pathlib import Path

import pandas as pd

from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate
from uticen_lite.store.run_service import run_control_in_store


def _seed(tmp_path: Path):
    (tmp_path / "data").mkdir()
    pd.DataFrame({"user_id": ["U1", "U2"], "can_create": ["true", "true"],
                  "can_approve": ["true", "false"]}).to_csv(
        tmp_path / "data" / "users.csv", index=False)
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme")
    repo.upsert_source(conn, id="users", format="csv", path="data/users.csv",
                       key_config={"mode": "single", "columns": ["user_id"]})
    repo.set_columns(conn, "users", [
        {"original_name": "user_id", "display_name": "User ID", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "can_create", "display_name": "Can Create",
         "data_type": "boolean", "is_key": False, "include": True, "ordinal": 1},
        {"original_name": "can_approve", "display_name": "Can Approve",
         "data_type": "boolean", "is_key": False, "include": True, "ordinal": 2},
    ])
    repo.upsert_control(conn, id="sod", title="SoD", objective="o", narrative="n",
                        framework_refs={"nist": ["AC-5"]}, test_kind="rule",
                        rule_spec={"logic": "all", "conditions": [
                            {"column": "can_create", "op": "eq", "value": True},
                            {"column": "can_approve", "op": "eq", "value": True}],
                            "severity": "high",
                            "description_template": "User {user_id}",
                            "item_key_column": "user_id"},
                        failure_threshold_count=0)
    repo.set_control_sources(conn, "sod", ["users"])
    return conn


def test_run_persists_and_renders(tmp_path: Path):
    conn = _seed(tmp_path)
    run = run_control_in_store(conn, tmp_path, "sod", "2026-03-31T00:00:00+00:00")
    assert run.failed == 1
    # persisted
    assert repo.latest_run(conn, "sod")["run_id"] == run.run_id
    # rendered
    assert (tmp_path / "target" / "workpapers" / "sod.html").exists()
    assert (tmp_path / "target" / "workpapers" / "sod.md").exists()
    assert (tmp_path / "target" / "evidence" / "sod-violations.json").exists()
    html = (tmp_path / "target" / "workpapers" / "sod.html").read_text()
    assert "<!doctype html>" in html.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/store/test_run_service.py -v`
Expected: FAIL — `ModuleNotFoundError: uticen_lite.store.run_service`.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/store/run_service.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from uticen_lite.model.run import RunRecord
from uticen_lite.model.workpaper import Workpaper
from uticen_lite.render.html import render_html
from uticen_lite.render.markdown import render_markdown
from uticen_lite.runner.execute import collect_data_samples, run_control
from uticen_lite.store import repo
from uticen_lite.store.loader import load_project_from_store


def run_control_in_store(
    conn: sqlite3.Connection, root: Path, control_id: str, executed_at: str
) -> RunRecord:
    project = load_project_from_store(conn)
    control = next((c for c in project.controls if c.id == control_id), None)
    if control is None:
        raise KeyError(f"no control {control_id!r} in store")

    run = run_control(control, project.sources, root, executed_at)
    repo.insert_run(conn, run)

    samples = collect_data_samples(control, project.sources, root)
    wp = Workpaper.assemble(control, run, generated_at=executed_at, data_samples=samples)

    wp_dir = root / "target" / "workpapers"
    ev_dir = root / "target" / "evidence"
    wp_dir.mkdir(parents=True, exist_ok=True)
    ev_dir.mkdir(parents=True, exist_ok=True)
    (wp_dir / f"{control_id}.html").write_text(render_html(wp), encoding="utf-8")
    (wp_dir / f"{control_id}.md").write_text(render_markdown(wp), encoding="utf-8")
    (ev_dir / f"{control_id}-violations.json").write_text(
        json.dumps([v.to_dict() for v in run.violations], indent=2), encoding="utf-8"
    )
    return run
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/store/test_run_service.py -v`
Expected: PASS.

> If `Workpaper.assemble` requires a `test_code` string for a rule control's procedure, pass the rendered rule: build it with `procedures` derived from `rule_to_text(parse_rule_spec(control.rule_spec))` when `control.test_kind == "rule"`. Check `Workpaper.assemble`'s use of `control.test_path` — if it reads the file, it must tolerate an empty path. If a test fails here, route the test text through `Procedure.test_code` (Task 15 covers the bundle equivalent; mirror it). Apply only if the assemble call raises.

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/store/run_service.py tests/store/test_run_service.py
git commit -m "feat(store): run a control from the store, persist + render"
git push -u origin HEAD
```

### Task 13: `uticen-lite import` (YAML project → store)

**Files:**
- Create: `uticen_lite/cli/import_cmd.py`
- Modify: `uticen_lite/cli/__init__.py` (register `import` subcommand)
- Test: `tests/cli/test_import_cmd.py`

**Interfaces:**
- Consumes: `project.discovery.Project.load`, `project.discovery.load_test_callable` reader is NOT used; reads `test_path` file bytes directly; `store.*`.
- Produces: `import_cmd(args: argparse.Namespace) -> int` — args: `src` (YAML project dir), `--into` (target engagement dir, default = `src`). Reads `cflow.yaml`/`sources.yaml`/`controls/*`, writes project/sources/columns/controls/bindings into `<into>/controlplane.db`. Python controls store `test_code` = the `test.py` file contents; the importer sets `test_kind="python"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_import_cmd.py
import argparse
from pathlib import Path

from uticen_lite.cli.import_cmd import import_cmd
from uticen_lite.store import repo
from uticen_lite.store.db import connect


def test_import_northwind(tmp_path: Path):
    src = Path("examples/northwind-trading").resolve()
    into = tmp_path / "engagement"
    rc = import_cmd(argparse.Namespace(src=str(src), into=str(into)))
    assert rc == 0
    conn = connect(into)
    controls = repo.list_controls(conn)
    sources = repo.list_sources(conn)
    assert len(controls) == 8
    assert len(sources) == 8
    # every imported control has runnable test_code and a binding
    for c in controls:
        assert c["test_kind"] == "python"
        assert c["test_code"] and "def test" in c["test_code"]
        assert c["source_ids"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cli/test_import_cmd.py -v`
Expected: FAIL — `ModuleNotFoundError: uticen_lite.cli.import_cmd`.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/cli/import_cmd.py
from __future__ import annotations

import argparse
from pathlib import Path

from uticen_lite.project.discovery import Project
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def import_cmd(args: argparse.Namespace) -> int:
    src = Path(args.src)
    into = Path(args.into) if getattr(args, "into", None) else src
    project = Project.load(src)

    conn = connect(into)
    migrate(conn)
    repo.upsert_project(conn, name=project.config.name,
                        framework=project.config.framework,
                        system=project.config.system or {})

    for sid, binding in project.sources.items():
        repo.upsert_source(
            conn, id=sid,
            format=binding.config.get("format", "csv"),
            path=binding.config.get("path", ""),
            key_config=binding.key_config,
            description=binding.description,
            completeness_accuracy=binding.completeness_accuracy,
            extract_date=binding.extract_date,
        )
        repo.set_columns(conn, sid, [
            {
                "original_name": m["original_name"],
                "display_name": m.get("display_name", m["original_name"]),
                "data_type": m.get("data_type", "text"),
                "is_key": bool(m.get("is_key")),
                "include": bool(m.get("include", True)),
                "ordinal": i,
            }
            for i, m in enumerate(binding.column_mappings)
        ])

    for control in project.controls:
        code = Path(control.test_path).read_text(encoding="utf-8") if control.test_path else ""
        repo.upsert_control(
            conn, id=control.id, title=control.title, objective=control.objective,
            narrative=control.narrative,
            framework_refs={"nist": control.framework_refs.nist,
                            **control.framework_refs.extra},
            test_kind="python", test_code=code,
            failure_threshold_pct=control.threshold.failure_threshold_pct,
            failure_threshold_count=control.threshold.failure_threshold_count,
        )
        repo.set_control_sources(conn, control.id, [s.id for s in control.sources])

    print(f"IMPORT  {len(project.controls)} controls / {len(project.sources)} sources → {into}")
    return 0
```

Register in `cli/__init__.py` `main()` argparse: add a subparser `import` with positional `src` and optional `--into`, dispatching to `import_cmd`. (Mirror the existing subparser registration style.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cli/test_import_cmd.py -v`
Expected: PASS.

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/cli/import_cmd.py uticen_lite/cli/__init__.py tests/cli/test_import_cmd.py
git commit -m "feat(cli): uticen-lite import — YAML project into controlplane.db"
git push -u origin HEAD
```

### Task 14: Route `uticen-lite run`/`build` through the store; drop `init`/`new`

**Files:**
- Modify: `uticen_lite/cli/run_cmd.py`, `uticen_lite/cli/build_cmd.py`, `uticen_lite/cli/__init__.py`
- Test: `tests/cli/test_run_cmd.py`, `tests/cli/test_build_cmd.py` (adjust to store), `tests/cli/test_cli.py` (drop init/new dispatch assertions)

**Interfaces:**
- Produces: `run_cmd`/`build_cmd` now obtain the `Project` via `load_project_from_store(connect(root))` instead of `Project.load(root)`. `run_cmd` persists runs via `store.run_service.run_control_in_store` (DB is the run ledger; `run-log.json` no longer the source for build). `build_cmd` reads runs from the store (`repo.list_runs_for` over all controls) and assembles the bundle. `main()` no longer registers `init`/`new`.

- [ ] **Step 1: Write the failing test** (store-backed run + build end to end)

```python
# tests/cli/test_build_cmd.py  (replace the YAML-project setup with an imported store)
import argparse
from pathlib import Path

from uticen_lite.cli.build_cmd import build_cmd
from uticen_lite.cli.import_cmd import import_cmd
from uticen_lite.cli.run_cmd import run_cmd
from uticen_lite.bundle.archive import read_bundle


def _engagement(tmp_path: Path) -> Path:
    into = tmp_path / "eng"
    import_cmd(argparse.Namespace(src="examples/northwind-trading", into=str(into)))
    # copy data files the imported sources point at
    import shutil
    shutil.copytree("examples/northwind-trading/data", into / "data")
    return into


def test_run_then_build_from_store(tmp_path: Path):
    root = _engagement(tmp_path)
    assert run_cmd(argparse.Namespace(dir=str(root), control=None,
                                      at="2026-03-31T00:00:00+00:00")) == 0
    out = root / "import-bundle.zip"
    assert build_cmd(argparse.Namespace(dir=str(root), out=str(out),
                                        at="2026-03-31T00:00:00+00:00")) == 0
    manifest = read_bundle(out)
    assert manifest["schema_version"] == "1.0"
    assert len(manifest["controls"]) == 8
    # contract conformance is asserted by tests/test_contract_export.py against the schema
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cli/test_build_cmd.py -k run_then_build_from_store -v`
Expected: FAIL — `run_cmd`/`build_cmd` still call `Project.load` (YAML) and won't find a YAML project in the engagement dir.

- [ ] **Step 3: Write minimal implementation**

In `run_cmd.py`: replace `project = Project.load(root)` with:

```python
    from uticen_lite.store.db import connect
    from uticen_lite.store.loader import load_project_from_store
    from uticen_lite.store.run_service import run_control_in_store

    conn = connect(root)
    project = load_project_from_store(conn)
    controls = project.controls
    if args.control:
        controls = [c for c in controls if c.id == args.control]
    for control in controls:
        run = run_control_in_store(conn, root, control.id, executed_at)
        print(f"  RUN  {control.id}  {run.failed} violation(s) / "
              f"{run.population_size} records  {run.pass_rate}%")
    return 0
```

(Delete the old per-control `run_control` + manual render + `append_run` block — `run_control_in_store` now does render + persist. Keep `executed_at` resolution and the error/exit-code handling.)

In `build_cmd.py`: replace `read_runs(target_dir)` with store reads:

```python
    from uticen_lite.store.db import connect
    from uticen_lite.store.loader import load_project_from_store
    from uticen_lite.store import repo

    conn = connect(root)
    project = load_project_from_store(conn)
    runs_by_control = {
        c.id: repo.list_runs_for(conn, c.id) for c in project.controls
    }
    runs_by_control = {cid: runs for cid, runs in runs_by_control.items() if runs}
    if not runs_by_control:
        print("No runs found. Run controls first with `uticen-lite run`.")
        return 1
    # store run dicts -> bundle run shape: reconstruct via RunRecord.to_dict()
    manifest = assemble_bundle(project, _to_run_dicts(runs_by_control), generated_at)
    write_bundle(manifest, target_dir, out_path)
```

Add a helper in `build_cmd.py` that turns store run dicts into the `RunRecord.to_dict()` shape `assemble_bundle` expects. Because `repo.get_run` already returns `run_id, executed_at, total, passed, failed, pass_rate, provenance, violations`, reconstruct a `RunRecord` and call `.to_dict()` for exact parity:

```python
def _to_run_dicts(runs_by_control: dict[str, list[dict]]) -> dict[str, list[dict]]:
    from uticen_lite.model.run import RunRecord, SourceProvenance
    from uticen_lite.model.violation import Violation

    out: dict[str, list[dict]] = {}
    for cid, runs in runs_by_control.items():
        rebuilt = []
        for r in runs:
            rr = RunRecord(
                control_id=r["control_id"], executed_at=r["executed_at"],
                population_size=r["population_size"],
                violations=[Violation.from_raw(v) for v in r["violations"]],
                provenance=[SourceProvenance(**p) for p in r["provenance"]],
            )
            rebuilt.append(rr.to_dict())
        out[cid] = rebuilt
    return out
```

In `cli/__init__.py` `main()`: remove the `init` and `new` subparsers and their `_cmd_init`/`_cmd_new` dispatch (delete those handlers); keep `validate` (now a light DB check is acceptable but out of scope — leave its YAML behavior or stub to return 0 over the store; minimal: leave `validate` registered but have it print a deprecation note and return 0). Keep `run`, `build`, add `import` (Task 13).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/cli/ -v`
Expected: PASS — store-backed run/build; update/remove any `test_cli.py` assertions that referenced `init`/`new` dispatch.

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/cli tests/cli
git commit -m "feat(cli): run/build operate over the store; retire init/new"
git push -u origin HEAD
```

### Task 15: Bundle projection resolves rule controls' `test_code`

**Files:**
- Modify: `uticen_lite/bundle/assemble.py` (test_code resolution)
- Test: `tests/bundle/test_assemble.py` (append) + rely on `tests/test_contract_export.py`

**Interfaces:**
- Produces: `assemble_bundle(project, runs_by_control, generated_at)` resolves each control's `test_code` as: `control.test_code` if set; else `rule_to_text(parse_rule_spec(control.rule_spec))` if `control.rule_spec`; else the file at `control.test_path` (existing behavior). The resulting manifest still validates against `contract/bundle.schema.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/bundle/test_assemble.py  (append)
from uticen_lite.bundle.assemble import assemble_bundle
from uticen_lite.model.control import ControlDef, FrameworkRefs
from uticen_lite.project.discovery import Project
from uticen_lite.project.loader import ProjectConfig


def test_rule_control_bundles_readable_test_code():
    control = ControlDef(
        id="sod", title="SoD", objective="o", narrative="n",
        framework_refs=FrameworkRefs(nist=["AC-5"]), risk=None, sources=[],
        rule_spec={"logic": "all", "conditions": [
            {"column": "can_create", "op": "eq", "value": True}], "severity": "high"},
    )
    project = Project(config=ProjectConfig(name="Acme", framework="nist"),
                      sources={}, controls=[control])
    run_dict = {
        "run_id": "0" * 16, "executed_at": "2026-03-31T00:00:00+00:00",
        "passed": 1, "failed": 0, "total": 1, "pass_rate": 100.0,
        "summary": "1/1 passed", "details": {"violations": []},
        "control_id": "sod", "provenance": [],
    }
    manifest = assemble_bundle(project, {"sod": [run_dict]}, "2026-03-31T00:00:00+00:00")
    block = next(c for c in manifest["controls"] if c["id"] == "sod")
    assert "Flag a record when ALL" in block["test_code"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bundle/test_assemble.py -k readable_test_code -v`
Expected: FAIL — `assemble_bundle` reads `control.test_path` (empty) → empty or error `test_code`.

- [ ] **Step 3: Write minimal implementation**

In `bundle/assemble.py`, where the control block's `test_code` is currently read from `test_path`, replace with a resolver:

```python
def _resolve_test_code(control) -> str:
    from uticen_lite.rules.render_rule import rule_to_text
    from uticen_lite.rules.spec import parse_rule_spec

    if getattr(control, "test_code", None):
        return control.test_code
    if getattr(control, "rule_spec", None):
        return rule_to_text(parse_rule_spec(control.rule_spec))
    if control.test_path:
        from pathlib import Path
        return Path(control.test_path).read_text(encoding="utf-8")
    return ""
```

Use `_resolve_test_code(control)` for the manifest's `test_code` field (and reuse it for the workpaper procedure's `test_code` if the assemble builds the workpaper inline).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bundle/ tests/test_contract_export.py -v`
Expected: PASS — including contract-schema conformance.

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/bundle/assemble.py tests/bundle/test_assemble.py
git commit -m "feat(bundle): resolve test_code from inline code or rendered rule"
git push -u origin HEAD
```

---

## Phase 4 — Web app (FastAPI + HTMX)

> Web tasks render server HTML; templates are concrete in their *contract* (the elements/ids the tests assert). Route handlers and `TestClient` tests carry the full code. Keep handlers thin — all persistence goes through `store.repo`; all execution through `store.run_service`.

### Task 16: App factory + `controlplane` entry + bootstrap

**Files:**
- Create: `uticen_lite/plane/__init__.py` (empty), `uticen_lite/plane/app.py`, `uticen_lite/plane/__main__.py`, `uticen_lite/plane/templates/base.html`, `uticen_lite/plane/templates/dashboard.html`, `uticen_lite/plane/static/app.css`
- Test: `tests/plane/__init__.py` (empty), `tests/plane/conftest.py`, `tests/plane/test_app.py`

**Interfaces:**
- Produces: `plane.app.create_app(project_root: Path) -> FastAPI` (ensures folder + `migrate`, mounts `/static`, registers routes, stores `project_root` on `app.state`). `plane.__main__.main(argv=None) -> int` (flags `--project`, `--host` default `127.0.0.1`, `--port` default `8765`, `--no-browser`; calls `uvicorn.run`). A per-request dependency yields a `sqlite3.Connection`.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/conftest.py
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uticen_lite.plane.app import create_app
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


@pytest.fixture
def engagement(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme")
    conn.close()
    return tmp_path


@pytest.fixture
def client(engagement: Path) -> TestClient:
    return TestClient(create_app(engagement))
```

```python
# tests/plane/test_app.py
def test_dashboard_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Acme" in resp.text
    assert "New control" in resp.text


def test_static_css_served(client):
    assert client.get("/static/app.css").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plane/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: uticen_lite.plane.app` (and fastapi import). Ensure `[plane]` deps are installed in the dev env: `pip install fastapi uvicorn jinja2 python-multipart` (Task 23 records them in pyproject).

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/plane/app.py
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def get_conn(request: Request) -> sqlite3.Connection:
    root: Path = request.app.state.project_root
    conn = connect(root)
    try:
        yield conn
    finally:
        conn.close()


def create_app(project_root: Path) -> FastAPI:
    project_root = Path(project_root)
    (project_root / "data").mkdir(parents=True, exist_ok=True)
    migrate(connect(project_root))

    app = FastAPI(title="Uticen Control Plane")
    app.state.project_root = project_root
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    from uticen_lite.plane.routes import controls, dashboard, export, runs, sources

    dashboard.register(app, templates, get_conn)
    sources.register(app, templates, get_conn)
    controls.register(app, templates, get_conn)
    runs.register(app, templates, get_conn)
    export.register(app, templates, get_conn)
    return app
```

```python
# uticen_lite/plane/routes/__init__.py  (empty)
```

```python
# uticen_lite/plane/routes/dashboard.py
from __future__ import annotations

import sqlite3

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse

from uticen_lite.store import repo


def register(app: FastAPI, templates, get_conn) -> None:
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        project = repo.get_project(conn) or {"name": ""}
        controls = repo.list_controls(conn)
        rows = []
        for c in controls:
            latest = repo.latest_run(conn, c["id"])
            rows.append({"control": c, "latest": latest})
        return templates.TemplateResponse(
            "dashboard.html",
            {"request": request, "project": project, "rows": rows},
        )
```

```html
<!-- uticen_lite/plane/templates/base.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Control Plane{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css">
  <script src="/static/htmx.min.js" defer></script>
</head>
<body>
  <header><strong>Uticen Control Plane</strong> — {{ project.name }}</header>
  <main>{% block body %}{% endblock %}</main>
</body>
</html>
```

```html
<!-- uticen_lite/plane/templates/dashboard.html -->
{% extends "base.html" %}
{% block title %}{{ project.name }} — Controls{% endblock %}
{% block body %}
<h1>Controls</h1>
<p>
  <a href="/controls/new">New control</a> ·
  <a href="/sources">New source</a> ·
  <a href="/export">Export bundle</a>
</p>
<table>
  <thead><tr><th>ID</th><th>Title</th><th>Sources</th><th>Last run</th><th></th></tr></thead>
  <tbody>
  {% for row in rows %}
    <tr>
      <td><a href="/controls/{{ row.control.id }}">{{ row.control.id }}</a></td>
      <td>{{ row.control.title }}</td>
      <td>{{ row.control.source_ids | join(", ") }}</td>
      <td>{% if row.latest %}{{ row.latest.failed }} / {{ row.latest.total }} ({{ row.latest.pass_rate }}%){% else %}—{% endif %}</td>
      <td>
        <form method="post" action="/controls/{{ row.control.id }}/run">
          <button type="submit">Run</button>
        </form>
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

```css
/* uticen_lite/plane/static/app.css */
:root { color-scheme: light; }
body { font-family: Inter, system-ui, sans-serif; margin: 0; }
header { padding: 12px 20px; border-bottom: 1px solid #ddd; }
main { padding: 20px; max-width: 980px; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #e2e2e2; padding: 6px 10px; text-align: left; }
```

Add a placeholder `static/htmx.min.js` (vendored in Task 23; an empty file is fine for Task 16 tests). 

```python
# uticen_lite/plane/__main__.py
from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="controlplane")
    parser.add_argument("--project", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    import uvicorn

    from uticen_lite.plane.app import create_app

    app = create_app(Path(args.project))
    if not args.no_browser:
        webbrowser.open(f"http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plane/test_app.py -v`
Expected: PASS (2 tests). Empty route modules `sources.py`/`controls.py`/`runs.py`/`export.py` each need a no-op `register(app, templates, get_conn): ...` to import cleanly; their real routes land in Tasks 18–22 — stub them now.

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/plane tests/plane
git commit -m "feat(plane): FastAPI app factory, dashboard, controlplane entry"
git push -u origin HEAD
```

### Task 17: Source manager — upload + column mapping

**Files:**
- Modify: `uticen_lite/plane/routes/sources.py`
- Create: `uticen_lite/plane/templates/sources.html`, `uticen_lite/plane/templates/source_edit.html`
- Test: `tests/plane/test_sources.py`

**Interfaces:**
- Produces routes: `GET /sources` (list + upload form), `POST /sources` (save an uploaded file to `data/`, read header row, create the source + a default column mapping, redirect to edit), `GET /sources/{id}` (column-mapping form), `POST /sources/{id}` (save mappings + key_config). Consumes `repo.upsert_source`/`set_columns`/`get_source`/`list_sources`.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_sources.py
import io


def test_upload_creates_source_with_inferred_columns(client):
    csv = b"user_id,can_create,can_approve\nU1,true,false\n"
    resp = client.post(
        "/sources",
        data={"source_id": "users", "format": "csv"},
        files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    edit = client.get("/sources/users")
    assert edit.status_code == 200
    for col in ("user_id", "can_create", "can_approve"):
        assert col in edit.text


def test_save_column_mapping(client):
    csv = b"user_id,amount\nU1,5\n"
    client.post("/sources", data={"source_id": "tx", "format": "csv"},
                files={"file": ("tx.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    resp = client.post("/sources/tx", data={
        "key_columns": "user_id",
        "display_name__user_id": "User ID", "data_type__user_id": "text",
        "is_key__user_id": "on", "include__user_id": "on",
        "display_name__amount": "Amount", "data_type__amount": "number",
        "include__amount": "on",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)
    # persisted
    from uticen_lite.store.db import connect
    from uticen_lite.store import repo
    src = repo.get_source(connect(client.app.state.project_root), "tx")
    assert src["key_config"] == {"mode": "single", "columns": ["user_id"]}
    amount = next(c for c in src["columns"] if c["original_name"] == "amount")
    assert amount["data_type"] == "number" and amount["display_name"] == "Amount"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plane/test_sources.py -v`
Expected: FAIL — routes return 404 (stub `register` does nothing).

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/plane/routes/sources.py
from __future__ import annotations

import csv as csvmod
import io
import sqlite3

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from uticen_lite.store import repo


def register(app: FastAPI, templates, get_conn) -> None:
    @app.get("/sources", response_class=HTMLResponse)
    def list_sources(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        return templates.TemplateResponse(
            "sources.html",
            {"request": request, "project": repo.get_project(conn) or {"name": ""},
             "sources": repo.list_sources(conn)},
        )

    @app.post("/sources")
    async def create_source(
        request: Request, source_id: str = Form(...), format: str = Form("csv"),
        file: UploadFile = File(...), conn: sqlite3.Connection = Depends(get_conn),
    ):
        root = request.app.state.project_root
        (root / "data").mkdir(parents=True, exist_ok=True)
        raw = await file.read()
        dest = root / "data" / file.filename
        dest.write_bytes(raw)
        header = next(csvmod.reader(io.StringIO(raw.decode("utf-8-sig"))), [])
        repo.upsert_source(conn, id=source_id, format=format,
                           path=f"data/{file.filename}", key_config={})
        repo.set_columns(conn, source_id, [
            {"original_name": h, "display_name": h, "data_type": "text",
             "is_key": False, "include": True, "ordinal": i}
            for i, h in enumerate(header)
        ])
        return RedirectResponse(f"/sources/{source_id}", status_code=303)

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    def edit_source(source_id: str, request: Request,
                    conn: sqlite3.Connection = Depends(get_conn)):
        return templates.TemplateResponse(
            "source_edit.html",
            {"request": request, "project": repo.get_project(conn) or {"name": ""},
             "source": repo.get_source(conn, source_id)},
        )

    @app.post("/sources/{source_id}")
    async def save_source(source_id: str, request: Request,
                          conn: sqlite3.Connection = Depends(get_conn)):
        form = await request.form()
        existing = repo.get_source(conn, source_id)
        key_columns = [k.strip() for k in str(form.get("key_columns", "")).split(",") if k.strip()]
        columns = []
        for i, col in enumerate(existing["columns"]):
            name = col["original_name"]
            columns.append({
                "original_name": name,
                "display_name": form.get(f"display_name__{name}", name),
                "data_type": form.get(f"data_type__{name}", "text"),
                "is_key": name in key_columns,
                "include": form.get(f"include__{name}") is not None,
                "ordinal": i,
            })
        repo.set_columns(conn, source_id, columns)
        key_config = {"mode": "single", "columns": key_columns} if len(key_columns) == 1 \
            else ({"mode": "composite", "columns": key_columns} if key_columns else {})
        repo.upsert_source(conn, id=source_id, format=existing["format"],
                           path=existing["path"], key_config=key_config)
        return RedirectResponse("/sources", status_code=303)
```

Templates `sources.html` (list + an upload form with fields `source_id`, `format`, `file`) and `source_edit.html` (a table of the source's columns, each row exposing `display_name__<name>`, `data_type__<name>` select, `is_key__<name>`/`include__<name>` checkboxes, plus a `key_columns` text field). Both extend `base.html`. The edit form must render each `original_name` as text (the tests assert the column names appear).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plane/test_sources.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/plane/routes/sources.py uticen_lite/plane/templates/sources.html uticen_lite/plane/templates/source_edit.html tests/plane/test_sources.py
git commit -m "feat(plane): source manager — upload + column mapping"
git push -u origin HEAD
```

### Task 18: Control editor — metadata + binding + python tab

**Files:**
- Modify: `uticen_lite/plane/routes/controls.py`
- Create: `uticen_lite/plane/templates/control_edit.html`
- Test: `tests/plane/test_controls.py`

**Interfaces:**
- Produces routes: `GET /controls/new` (blank editor), `GET /controls/{id}` (editor populated), `POST /controls` (create), `POST /controls/{id}` (update). The POST accepts: `id, title, objective, narrative, framework_nist` (comma list), `failure_threshold_pct`/`failure_threshold_count`, `source_ids` (multi), `test_kind` (`rule|python`), `test_code` (when python), and rule fields (Task 19). Persists via `repo.upsert_control` + `repo.set_control_sources`.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_controls.py
import io


def _make_source(client, sid="users"):
    csv = b"user_id,can_create,can_approve\nU1,true,false\n"
    client.post("/sources", data={"source_id": sid, "format": "csv"},
                files={"file": (f"{sid}.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)


def test_create_python_control(client):
    _make_source(client)
    resp = client.post("/controls", data={
        "id": "py1", "title": "Py", "objective": "o", "narrative": "n",
        "framework_nist": "AC-2, AC-5", "test_kind": "python",
        "test_code": "def test(pop):\n    return []",
        "source_ids": ["users"],
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)
    from uticen_lite.store.db import connect
    from uticen_lite.store import repo
    c = repo.get_control(connect(client.app.state.project_root), "py1")
    assert c["test_kind"] == "python"
    assert c["framework_refs"] == {"nist": ["AC-2", "AC-5"]}
    assert c["source_ids"] == ["users"]


def test_edit_control_shows_values(client):
    _make_source(client)
    client.post("/controls", data={
        "id": "py2", "title": "Editable", "objective": "o", "narrative": "n",
        "test_kind": "python", "test_code": "def test(pop):\n    return []",
        "source_ids": ["users"]}, follow_redirects=False)
    page = client.get("/controls/py2")
    assert page.status_code == 200
    assert "Editable" in page.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plane/test_controls.py -v`
Expected: FAIL — routes 404.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/plane/routes/controls.py
from __future__ import annotations

import sqlite3

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from uticen_lite.store import repo


def _ctx(conn, request, control):
    return {"request": request, "project": repo.get_project(conn) or {"name": ""},
            "control": control, "sources": repo.list_sources(conn)}


def _save_from_form(conn, form) -> str:
    cid = str(form.get("id")).strip()
    nist = [s.strip() for s in str(form.get("framework_nist", "")).split(",") if s.strip()]
    test_kind = form.get("test_kind", "rule")
    rule_spec = _rule_spec_from_form(form) if test_kind == "rule" else None
    test_code = form.get("test_code") if test_kind == "python" else None
    pct = form.get("failure_threshold_pct")
    cnt = form.get("failure_threshold_count")
    repo.upsert_control(
        conn, id=cid, title=form.get("title", ""), objective=form.get("objective", ""),
        narrative=form.get("narrative", ""), framework_refs={"nist": nist},
        test_kind=test_kind, rule_spec=rule_spec, test_code=test_code,
        failure_threshold_pct=float(pct) if pct else None,
        failure_threshold_count=int(cnt) if cnt else None,
    )
    repo.set_control_sources(conn, cid, form.getlist("source_ids"))
    return cid


def register(app: FastAPI, templates, get_conn) -> None:
    @app.get("/controls/new", response_class=HTMLResponse)
    def new_control(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        return templates.TemplateResponse("control_edit.html", _ctx(conn, request, None))

    @app.get("/controls/{control_id}", response_class=HTMLResponse)
    def edit_control(control_id: str, request: Request,
                     conn: sqlite3.Connection = Depends(get_conn)):
        return templates.TemplateResponse(
            "control_edit.html", _ctx(conn, request, repo.get_control(conn, control_id)))

    @app.post("/controls")
    async def create_control(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        cid = _save_from_form(conn, await request.form())
        return RedirectResponse(f"/controls/{cid}", status_code=303)

    @app.post("/controls/{control_id}")
    async def update_control(control_id: str, request: Request,
                             conn: sqlite3.Connection = Depends(get_conn)):
        _save_from_form(conn, await request.form())
        return RedirectResponse(f"/controls/{control_id}", status_code=303)
```

Add `_rule_spec_from_form` in this module (used by Task 19; for now a stub returning `{"logic": "all", "conditions": [], "severity": "medium"}` is sufficient to make the python tests pass — Task 19 fills it in):

```python
def _rule_spec_from_form(form) -> dict:
    return {"logic": form.get("rule_logic", "all"), "conditions": [],
            "severity": form.get("rule_severity", "medium"),
            "description_template": form.get("rule_description", ""),
            "item_key_column": form.get("rule_item_key") or None}
```

`control_edit.html` renders the metadata form (text fields for id/title/objective/narrative/framework_nist/thresholds), a multi-select / checkbox list of `sources` for `source_ids`, a `test_kind` radio (`rule`/`python`), the Python `<textarea name="test_code">` (CodeMirror enhances it client-side — Task 23 vendors it), and the rule-builder partial include (Task 19). When `control` is set, fields are pre-filled (the test asserts the title text appears).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plane/test_controls.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/plane/routes/controls.py uticen_lite/plane/templates/control_edit.html tests/plane/test_controls.py
git commit -m "feat(plane): control editor — metadata, binding, python tab"
git push -u origin HEAD
```

### Task 19: Rule builder (HTMX condition rows → rule_spec)

**Files:**
- Modify: `uticen_lite/plane/routes/controls.py` (`_rule_spec_from_form` + an HTMX partial route)
- Create: `uticen_lite/plane/templates/partials/rule_condition.html`, `uticen_lite/plane/templates/partials/rule_builder.html`
- Test: `tests/plane/test_rule_builder.py`

**Interfaces:**
- Produces: `_rule_spec_from_form(form)` reads repeated fields `cond_column`, `cond_op`, `cond_value` (parallel lists) into `conditions`, applying `rule_logic`/`rule_severity`/`rule_description`/`rule_item_key`. Route `GET /controls/_condition_row` returns one blank condition row partial (HTMX "add condition"). Values are typed: `true`/`false` → bool, numeric → number, else string; `in`/`not_in` split on `|`.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_rule_builder.py
import io


def _src(client):
    csv = b"user_id,can_create,can_approve\nU1,true,true\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)


def test_rule_builder_builds_spec_from_conditions(client):
    _src(client)
    client.post("/controls", data={
        "id": "sod", "title": "SoD", "objective": "o", "narrative": "n",
        "test_kind": "rule", "rule_logic": "all", "rule_severity": "high",
        "rule_description": "User {user_id} can create and approve",
        "rule_item_key": "user_id",
        "cond_column": ["can_create", "can_approve"],
        "cond_op": ["eq", "eq"],
        "cond_value": ["true", "true"],
        "source_ids": ["users"],
    }, follow_redirects=False)
    from uticen_lite.store.db import connect
    from uticen_lite.store import repo
    c = repo.get_control(connect(client.app.state.project_root), "sod")
    assert c["test_kind"] == "rule"
    spec = c["rule_spec"]
    assert spec["logic"] == "all" and spec["severity"] == "high"
    assert spec["conditions"] == [
        {"column": "can_create", "op": "eq", "value": True},
        {"column": "can_approve", "op": "eq", "value": True},
    ]
    assert spec["item_key_column"] == "user_id"


def test_add_condition_row_partial(client):
    resp = client.get("/controls/_condition_row")
    assert resp.status_code == 200
    assert 'name="cond_column"' in resp.text
    assert 'name="cond_op"' in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plane/test_rule_builder.py -v`
Expected: FAIL — `_rule_spec_from_form` returns empty conditions; `/controls/_condition_row` 404.

- [ ] **Step 3: Write minimal implementation**

Replace the stub `_rule_spec_from_form` with the real parser, and add the partial route inside `register`:

```python
def _typed(value: str):
    v = value.strip()
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return v


def _rule_spec_from_form(form) -> dict:
    columns = form.getlist("cond_column")
    ops = form.getlist("cond_op")
    values = form.getlist("cond_value")
    conditions = []
    for col, op, raw in zip(columns, ops, values):
        if not col:
            continue
        cond: dict = {"column": col, "op": op}
        if op in ("is_empty", "not_empty", "is_duplicate"):
            pass
        elif op in ("in", "not_in"):
            cond["value"] = [_typed(p) for p in raw.split("|") if p.strip()]
        else:
            cond["value"] = _typed(raw)
        conditions.append(cond)
    return {
        "logic": form.get("rule_logic", "all"),
        "conditions": conditions,
        "severity": form.get("rule_severity", "medium"),
        "description_template": form.get("rule_description", ""),
        "item_key_column": form.get("rule_item_key") or None,
    }
```

Add inside `register`:

```python
    @app.get("/controls/_condition_row", response_class=HTMLResponse)
    def condition_row(request: Request):
        return templates.TemplateResponse(
            "partials/rule_condition.html", {"request": request})
```

`partials/rule_condition.html` renders one row: a `name="cond_column"` input, a `name="cond_op"` `<select>` over the 12 operators, and a `name="cond_value"` input. `partials/rule_builder.html` wraps `rule_logic`/`rule_severity`/`rule_description`/`rule_item_key` fields, a `#conditions` container seeded with existing conditions (when editing) and one blank row, and an "Add condition" button: `<button hx-get="/controls/_condition_row" hx-target="#conditions" hx-swap="beforeend">`. Include `rule_builder.html` in `control_edit.html`'s rule tab.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plane/test_rule_builder.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/plane/routes/controls.py uticen_lite/plane/templates/partials tests/plane/test_rule_builder.py
git commit -m "feat(plane): no-code rule builder → rule_spec (HTMX condition rows)"
git push -u origin HEAD
```

### Task 20: Run + run view (embeds the workpaper)

**Files:**
- Modify: `uticen_lite/plane/routes/runs.py`
- Create: `uticen_lite/plane/templates/run_view.html`
- Test: `tests/plane/test_runs.py`

**Interfaces:**
- Consumes: `store.run_service.run_control_in_store`, `store.repo.get_run`.
- Produces routes: `POST /controls/{id}/run` (runs with an injected `executed_at = datetime.now(UTC).isoformat()`, redirects to the run view), `GET /controls/{id}/runs/{run_id}` (totals + violations table + the workpaper HTML read from `target/workpapers/{id}.html`, embedded in an `<iframe srcdoc>` or a sanitized container).

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_runs.py
import io


def _rule_control(client):
    csv = b"user_id,can_create,can_approve\nU1,true,true\nU2,true,false\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={
        "id": "sod", "title": "SoD", "objective": "o", "narrative": "n",
        "test_kind": "rule", "rule_logic": "all", "rule_severity": "high",
        "rule_description": "User {user_id}", "rule_item_key": "user_id",
        "cond_column": ["can_create", "can_approve"], "cond_op": ["eq", "eq"],
        "cond_value": ["true", "true"], "source_ids": ["users"],
        "failure_threshold_count": "0",
    }, follow_redirects=False)


def test_run_then_view(client):
    _rule_control(client)
    resp = client.post("/controls/sod/run", follow_redirects=False)
    assert resp.status_code in (302, 303)
    run_url = resp.headers["location"]
    view = client.get(run_url)
    assert view.status_code == 200
    assert "U1" in view.text                 # the one violation
    assert "1" in view.text                  # failed count present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plane/test_runs.py -v`
Expected: FAIL — routes 404.

- [ ] **Step 3: Write minimal implementation**

```python
# uticen_lite/plane/routes/runs.py
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from uticen_lite.store import repo
from uticen_lite.store.run_service import run_control_in_store


def register(app: FastAPI, templates, get_conn) -> None:
    @app.post("/controls/{control_id}/run")
    def run(control_id: str, request: Request,
            conn: sqlite3.Connection = Depends(get_conn)):
        root = request.app.state.project_root
        executed_at = datetime.now(UTC).isoformat()
        rec = run_control_in_store(conn, root, control_id, executed_at)
        return RedirectResponse(f"/controls/{control_id}/runs/{rec.run_id}",
                                status_code=303)

    @app.get("/controls/{control_id}/runs/{run_id}", response_class=HTMLResponse)
    def run_view(control_id: str, run_id: str, request: Request,
                 conn: sqlite3.Connection = Depends(get_conn)):
        root = request.app.state.project_root
        run = repo.get_run(conn, run_id)
        wp_path = root / "target" / "workpapers" / f"{control_id}.html"
        workpaper_html = wp_path.read_text(encoding="utf-8") if wp_path.exists() else ""
        return templates.TemplateResponse(
            "run_view.html",
            {"request": request, "project": repo.get_project(conn) or {"name": ""},
             "control_id": control_id, "run": run, "workpaper_html": workpaper_html},
        )
```

`run_view.html` shows the totals (`run.failed` / `run.total` / `run.pass_rate`), a table over `run.violations` (item_key, description, severity), and embeds the workpaper via `<iframe srcdoc="{{ workpaper_html | e }}" ...>` (escaping keeps it inert and self-contained). The test asserts the violating `item_key` ("U1") and the failed count appear in the page (the violations table provides both, independent of the iframe).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plane/test_runs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/plane/routes/runs.py uticen_lite/plane/templates/run_view.html tests/plane/test_runs.py
git commit -m "feat(plane): run a control + run view with embedded workpaper"
git push -u origin HEAD
```

### Task 21: Export bundle from the web app

**Files:**
- Modify: `uticen_lite/plane/routes/export.py`
- Test: `tests/plane/test_export.py`

**Interfaces:**
- Consumes: the same store-backed assembly as `build_cmd` (Task 14). Factor the build into a reusable `uticen_lite/store/export_service.py:build_bundle(conn, root, out_path, generated_at) -> Path` and call it from both `build_cmd` and this route. (If Task 14 inlined the logic, extract it here and have `build_cmd` import it — note the small refactor.)
- Produces route: `GET /export` (a page with a button), `POST /export` (builds `target/bundle.zip`, returns it as a `FileResponse` download).

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_export.py
import io
import zipfile
import json


def _ran_control(client):
    csv = b"user_id,can_create,can_approve\nU1,true,true\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={
        "id": "sod", "title": "SoD", "objective": "o", "narrative": "n",
        "test_kind": "rule", "rule_logic": "all", "rule_severity": "high",
        "rule_description": "User {user_id}", "rule_item_key": "user_id",
        "cond_column": ["can_create"], "cond_op": ["eq"], "cond_value": ["true"],
        "source_ids": ["users"], "failure_threshold_count": "0"}, follow_redirects=False)
    client.post("/controls/sod/run", follow_redirects=False)


def test_export_returns_valid_bundle(client):
    _ran_control(client)
    resp = client.post("/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] in ("application/zip", "application/x-zip-compressed")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["schema_version"] == "1.0"
    assert any(c["id"] == "sod" for c in manifest["controls"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plane/test_export.py -v`
Expected: FAIL — route 404.

- [ ] **Step 3: Write minimal implementation**

Create `uticen_lite/store/export_service.py`:

```python
# uticen_lite/store/export_service.py
from __future__ import annotations

import sqlite3
from pathlib import Path

from uticen_lite.bundle.archive import write_bundle
from uticen_lite.bundle.assemble import assemble_bundle
from uticen_lite.store import repo
from uticen_lite.store.loader import load_project_from_store


def _to_run_dicts(conn, controls) -> dict[str, list[dict]]:
    from uticen_lite.model.run import RunRecord, SourceProvenance
    from uticen_lite.model.violation import Violation

    out: dict[str, list[dict]] = {}
    for c in controls:
        runs = repo.list_runs_for(conn, c.id)
        if not runs:
            continue
        rebuilt = []
        for r in runs:
            rr = RunRecord(
                control_id=r["control_id"], executed_at=r["executed_at"],
                population_size=r["population_size"],
                violations=[Violation.from_raw(v) for v in r["violations"]],
                provenance=[SourceProvenance(**p) for p in r["provenance"]],
            )
            rebuilt.append(rr.to_dict())
        out[c.id] = rebuilt
    return out


def build_bundle(conn: sqlite3.Connection, root: Path, out_path: Path,
                 generated_at: str) -> Path:
    project = load_project_from_store(conn)
    runs_by_control = _to_run_dicts(conn, project.controls)
    if not runs_by_control:
        raise ValueError("no runs to export")
    manifest = assemble_bundle(project, runs_by_control, generated_at)
    return write_bundle(manifest, root / "target", out_path)
```

Refactor `build_cmd.py` (Task 14) to call `build_bundle` instead of its inline `_to_run_dicts`. Then the route:

```python
# uticen_lite/plane/routes/export.py
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse

from uticen_lite.store import repo
from uticen_lite.store.export_service import build_bundle


def register(app: FastAPI, templates, get_conn) -> None:
    @app.get("/export", response_class=HTMLResponse)
    def export_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        return templates.TemplateResponse(
            "export.html",
            {"request": request, "project": repo.get_project(conn) or {"name": ""}})

    @app.post("/export")
    def export(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        root = request.app.state.project_root
        out = root / "target" / "bundle.zip"
        build_bundle(conn, root, out, datetime.now(UTC).isoformat())
        return FileResponse(out, media_type="application/zip", filename="bundle.zip")
```

Add a minimal `export.html` (one `<form method="post" action="/export"><button>Export bundle</button></form>`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plane/test_export.py -v`
Expected: PASS.

- [ ] **Step 5: Commit + push**

```bash
git add uticen_lite/store/export_service.py uticen_lite/cli/build_cmd.py uticen_lite/plane/routes/export.py uticen_lite/plane/templates/export.html tests/plane/test_export.py
git commit -m "feat(plane): export bundle from the web app (shared build_bundle)"
git push -u origin HEAD
```

---

## Phase 5 — Packaging, demo, docs

### Task 22: Package the `[plane]` extra + `controlplane` entry + vendor CodeMirror

**Files:**
- Modify: `pyproject.toml`
- Create: `uticen_lite/plane/static/codemirror.min.js`, `uticen_lite/plane/static/codemirror.min.css`, `uticen_lite/plane/static/codemirror-python.min.js`, `uticen_lite/plane/static/htmx.min.js` (vendored library files)
- Test: `tests/plane/test_packaging.py`

**Interfaces:**
- Produces: `[project.optional-dependencies] plane = ["fastapi>=0.110","uvicorn>=0.27","jinja2>=3.1","python-multipart>=0.0.9"]`; `[project.scripts] controlplane = "uticen_lite.plane.__main__:main"`; hatch wheel includes `plane/templates/**` and `plane/static/**`.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_packaging.py
import tomllib
from pathlib import Path


def test_pyproject_declares_plane_extra_and_entry():
    data = tomllib.loads(Path("pyproject.toml").read_text())
    extras = data["project"]["optional-dependencies"]
    assert "plane" in extras
    joined = " ".join(extras["plane"])
    for dep in ("fastapi", "uvicorn", "jinja2", "python-multipart"):
        assert dep in joined
    assert data["project"]["scripts"]["controlplane"] == "uticen_lite.plane.__main__:main"


def test_main_entrypoint_importable():
    from uticen_lite.plane.__main__ import main
    assert callable(main)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plane/test_packaging.py -v`
Expected: FAIL — `plane` not in extras / `controlplane` not in scripts.

- [ ] **Step 3: Write minimal implementation**

Edit `pyproject.toml`:

```toml
[project.optional-dependencies]
adapters = ["openpyxl>=3.1", "pyarrow>=14.0"]
plane = ["fastapi>=0.110", "uvicorn>=0.27", "jinja2>=3.1", "python-multipart>=0.0.9"]
dev = ["pytest>=8.0", "ruff>=0.5", "mypy>=1.8", "types-PyYAML", "types-jsonschema",
  "pandas-stubs>=2.0", "build>=1.0", "twine>=5.0",
  "fastapi>=0.110", "uvicorn>=0.27", "jinja2>=3.1", "python-multipart>=0.0.9",
  "httpx>=0.27"]   # httpx for fastapi TestClient

[project.scripts]
uticen-lite = "uticen_lite.cli:main"
controlplane = "uticen_lite.plane.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["uticen_lite"]

[tool.hatch.build.targets.wheel.force-include]
"uticen_lite/plane/templates" = "uticen_lite/plane/templates"
"uticen_lite/plane/static" = "uticen_lite/plane/static"
```

Vendor the real minified library files into `plane/static/` (download once into the repo; they are committed assets, no CDN at runtime): `htmx.min.js` (htmx 1.x), `codemirror.min.js` + `codemirror.min.css` + the Python mode. `control_edit.html` references them with `<link>`/`<script src="/static/...">` and initializes CodeMirror over the `test_code` textarea.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plane/test_packaging.py -v`
Expected: PASS.

- [ ] **Step 5: Commit + push**

```bash
git add pyproject.toml uticen_lite/plane/static tests/plane/test_packaging.py
git commit -m "build: package [plane] extra, controlplane entry, vendor CodeMirror/HTMX"
git push -u origin HEAD
```

### Task 23: Northwind demo via import + README rewrite

**Files:**
- Modify: `tests/examples/test_northwind.py` (exercise import → run → build over the store)
- Modify: `README.md` (control-plane quickstart; retire `init`/`new` docs; keep authoring reference)
- Test: the modified `tests/examples/test_northwind.py`

**Interfaces:**
- Consumes: `cli.import_cmd.import_cmd`, `cli.run_cmd.run_cmd`, `cli.build_cmd.build_cmd`.

- [ ] **Step 1: Write the failing test** (rewrite the example test to the store path)

```python
# tests/examples/test_northwind.py  (replace the body)
import argparse
import shutil
from pathlib import Path

from uticen_lite.bundle.archive import read_bundle
from uticen_lite.cli.build_cmd import build_cmd
from uticen_lite.cli.import_cmd import import_cmd
from uticen_lite.cli.run_cmd import run_cmd


def test_northwind_import_run_build(tmp_path: Path):
    eng = tmp_path / "northwind"
    import_cmd(argparse.Namespace(src="examples/northwind-trading", into=str(eng)))
    shutil.copytree("examples/northwind-trading/data", eng / "data")

    assert run_cmd(argparse.Namespace(dir=str(eng), control=None,
                                      at="2026-03-31T00:00:00Z")) == 0
    out = eng / "import-bundle.zip"
    assert build_cmd(argparse.Namespace(dir=str(eng), out=str(out),
                                        at="2026-03-31T00:00:00Z")) == 0
    manifest = read_bundle(out)
    assert len(manifest["controls"]) == 8
    assert sum(len(c["runs"]) for c in manifest["controls"]) == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/examples/test_northwind.py -v`
Expected: FAIL if the old test asserted the YAML `uticen-lite run` path; now it exercises import→run→build.

- [ ] **Step 3: Make it pass + rewrite README**

Ensure the import→run→build path is green (it depends on Tasks 13–15). Then rewrite `README.md`:
- Lead with the control plane: `pip install 'uticen-lite[plane]'` → `controlplane` → author in the browser.
- "See it in action" becomes: `uticen-lite import examples/northwind-trading --into demo` → `controlplane --project demo` (or `uticen-lite run demo` / `uticen-lite build demo` headless).
- Replace the `uticen-lite init` / `uticen-lite new control` authoring sections with the web-app workflow (new source, new control, rule builder, Python escape hatch) and keep the `Population` / data-types / key-config reference.
- State the brittle-by-design folder convention and the localhost/offline guarantee.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q && ruff check . && mypy uticen_lite`
Expected: all green.

- [ ] **Step 5: Commit + push**

```bash
git add tests/examples/test_northwind.py README.md
git commit -m "docs: control-plane quickstart; Northwind via import; retire init/new"
git push -u origin HEAD
```

---

## Self-Review (run before handing off to execution)

**1. Spec coverage:** every spec section maps to a task —
- SQLite source of truth + schema → Tasks 1, 3–5
- ControlDef inline code/rule → Task 2
- Store loader → Task 6
- Rule engine (spec, evaluator, render) → Tasks 7–9
- Runner branch + inline code → Tasks 10–11
- Run + persist + render → Task 12
- `uticen-lite import` + headless run/build over store + retire init/new → Tasks 13–14
- Bundle projection (contract conformance) → Task 15
- Web app: app/dashboard/sources/control editor/rule builder/run view/export → Tasks 16–21
- Packaging + Northwind demo + README + non-goals respected → Tasks 22–23

**2. Placeholder scan:** code shown for every code step; remaining template bodies are specified by contract (the elements the tests assert) rather than full HTML — acceptable because the route + TestClient assertions are the reviewable gate. No "TBD"/"handle edge cases".

**3. Type consistency:** `repo.*`, `RuleSpec`/`Condition`, `evaluate_rule(spec, pop)`, `load_project_from_store(conn)`, `run_control_in_store(conn, root, control_id, executed_at)`, `build_bundle(conn, root, out_path, generated_at)`, `_to_run_dicts` (shared) — names/signatures consistent across tasks. `ControlDef(test_path="", test_code=None, rule_spec=None)` used uniformly.

**Known follow-ups noted inline (apply only if a test fails):** `Workpaper.assemble` tolerating an empty `test_path` for rule controls (Task 12 note); numpy-scalar JSON coercion in `evaluate_rule` details (Task 8 note).
