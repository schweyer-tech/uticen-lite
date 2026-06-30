"""Shared engagement-import logic reused by ``uticen-lite import`` and the control plane.

The CLI (`uticen_lite.cli.import_cmd`) and the web first-run flow
(`uticen_lite.plane.routes.setup`) both turn a YAML project directory into rows
in a ``controlplane.db`` store. Keep that logic here so there is exactly one import
path — see the import contract in the engagement store.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from uticen_lite.model.control import SourceBinding
from uticen_lite.pipeline.compile import compile_pipeline
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.project.discovery import Project
from uticen_lite.store import repo


def _import_stamp() -> str:
    """Upload/as-of stamp for imported files (same wire format the UI renders)."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _row_count_of(binding: SourceBinding, root: Path) -> int | None:
    """Count a source file's data rows for the store-only file history.

    Reuses the format-aware adapter machinery (``source_for(...).provenance()``)
    so csv/parquet/xlsx all report an accurate count. Returns ``None`` if the file
    can't be read (missing extract, unsupported format, optional dep absent) so an
    import never fails just because a count couldn't be derived — the History tab
    falls back to "—" in that case.
    """
    from uticen_lite.adapters.files import source_for

    try:
        prov = source_for(binding, root).provenance()
        count = prov.get("row_count")
        return int(count) if count is not None else None
    except Exception:
        return None


def import_project(conn: sqlite3.Connection, src: Path) -> tuple[int, int]:
    """Load a YAML project directory into an already-migrated store.

    Writes the project metadata, sources (+ column mappings), and controls (+ source
    bindings) from *src* into *conn*. The caller owns migration and the connection.

    Args:
        conn: An open, migrated connection to the target engagement store.
        src:  Path to the YAML project directory (holds ``cflow.yaml``,
            ``sources.yaml``, and ``controls/*/``).

    Returns:
        ``(n_controls, n_sources)`` imported.
    """
    src = Path(src)
    project = Project.load(src)

    repo.upsert_project(
        conn,
        name=project.config.name,
        framework=project.config.framework,
        system=project.config.system or {},
    )

    for sid, binding in project.sources.items():
        repo.upsert_source(
            conn,
            id=sid,
            format=binding.config.get("format", "csv"),
            path=binding.config.get("path", ""),
            key_config=binding.key_config,
            title=binding.title,
            description=binding.description,
            completeness_accuracy=binding.completeness_accuracy,
            extract_date=binding.extract_date,
            sheet=binding.config.get("sheet"),
        )
        repo.set_columns(
            conn,
            sid,
            [
                {
                    "original_name": m["original_name"],
                    "display_name": m.get("display_name", m["original_name"]),
                    "data_type": m.get("data_type", "text"),
                    "is_key": bool(m.get("is_key")),
                    "include": bool(m.get("include", True)),
                    "ordinal": i,
                }
                for i, m in enumerate(binding.column_mappings)
            ],
        )
        _path = binding.config.get("path", "")
        repo.set_initial_file(
            conn,
            source_id=sid,
            stored_path=_path,
            original_name=Path(_path).name,
            as_of_date=binding.extract_date,
            row_count=_row_count_of(binding, src),
            uploaded_at=_import_stamp(),
        )

    for control in project.controls:
        authoring = _resolve_authoring(control)
        repo.upsert_control(
            conn,
            id=control.id,
            title=control.title,
            objective=control.objective,
            narrative=control.narrative,
            framework_refs={
                "nist": control.framework_refs.nist,
                **control.framework_refs.extra,
            },
            test_kind=authoring["test_kind"],
            rule_spec=authoring["rule_spec"],
            test_code=authoring["test_code"],
            pipeline=authoring["pipeline"],
            failure_threshold_pct=control.threshold.failure_threshold_pct,
            failure_threshold_count=control.threshold.failure_threshold_count,
            failure_threshold_rationale=control.threshold.rationale,
        )
        repo.set_control_sources(conn, control.id, [s.id for s in control.sources])

    return len(project.controls), len(project.sources)


def _resolve_authoring(control: object) -> dict[str, Any]:
    """Pick a control's authoring mode from optional sidecars next to ``control.yaml``.

    Each control directory holds the ``test.py`` Python escape hatch by default. To
    let the bundled demo showcase the no-code and visual authoring surfaces (not only
    the escape hatch), a control may *also* ship one of two store-only sidecars; the
    importer prefers the sidecar and stores the richer ``test_kind`` so the loaded
    engagement shows a MIX of authoring modes:

    * ``rule.yaml``     — a no-code ``rule_spec`` (``test_kind == "rule"``).
    * ``pipeline.yaml`` — a visual pipeline graph; it is parsed/validated, kept in the
      store-only ``pipeline`` column, and COMPILED to the existing bundle artifact
      (a ``rule_spec`` for the pure single-source case, else generated ``test_code``)
      so ``test_kind == "pipeline"`` (learning 0010 — the bundle never sees the graph).

    Falls back to the file-based ``test.py`` (``test_kind == "python"``) when no
    sidecar is present. Sidecars are an *authoring representation* only — they do not
    touch ``bundle.schema.json`` — and ``uticen-lite``/the web runner reuse the unchanged
    rule/python execution paths against whatever lands in ``rule_spec``/``test_code``.
    """
    test_path = getattr(control, "test_path", "") or ""
    control_dir = Path(test_path).parent if test_path else None

    if control_dir is not None:
        rule_file = control_dir / "rule.yaml"
        if rule_file.is_file():
            rule_spec = yaml.safe_load(rule_file.read_text(encoding="utf-8")) or {}
            return {
                "test_kind": "rule",
                "rule_spec": rule_spec,
                "test_code": None,
                "pipeline": None,
            }

        pipeline_file = control_dir / "pipeline.yaml"
        if pipeline_file.is_file():
            graph = yaml.safe_load(pipeline_file.read_text(encoding="utf-8")) or {}
            pipeline = parse_pipeline(graph)  # validate the graph eagerly
            compiled = compile_pipeline(pipeline)
            return {
                "test_kind": "pipeline",
                "rule_spec": compiled.rule_spec,
                "test_code": compiled.test_code,
                "pipeline": graph,
            }

    code = Path(test_path).read_text(encoding="utf-8") if test_path else ""
    return {"test_kind": "python", "rule_spec": None, "test_code": code, "pipeline": None}


def demo_source_dir() -> Path:
    """Locate the bundled Northwind demo engagement.

    Resolves the packaged copy first (``uticen_lite/_demo/northwind-trading``,
    force-included into the wheel for pip-installed users) and falls back to the
    repo's ``examples/northwind-trading`` for editable/source checkouts where the
    force-include has not run. The two are the same content; ``examples/`` is the
    single source of truth and the build maps it into the package.

    Raises:
        FileNotFoundError: if neither location exists.
    """
    here = Path(__file__).resolve()
    packaged = here.parent.parent / "_demo" / "northwind-trading"
    if packaged.is_dir():
        return packaged
    repo_example = here.parent.parent.parent / "examples" / "northwind-trading"
    if repo_example.is_dir():
        return repo_example
    raise FileNotFoundError(
        "Northwind demo not found in package (_demo/) or repo (examples/). "
        "Reinstall uticen-lite or run from a source checkout."
    )


def load_demo(conn: sqlite3.Connection, root: Path) -> tuple[int, int]:
    """Populate *root*'s engagement with the runnable Northwind demo.

    Imports the demo project into *conn* and copies its data extracts into
    ``root/data/`` so the stored ``data/*.csv`` source paths resolve when a control
    is run. The caller owns migration and the connection.

    Args:
        conn: An open, migrated connection to the target engagement store.
        root: The engagement directory (the ``--project`` dir) whose ``data/``
            should receive the demo CSVs.

    Returns:
        ``(n_controls, n_sources)`` imported.
    """
    src = demo_source_dir()
    counts = import_project(conn, src)

    # The demo's project name is a slug ("northwind-trading"); prefer the friendlier
    # display name from system.name for the engagement header on the setup screen.
    project = repo.get_project(conn) or {}
    display = (project.get("system") or {}).get("name")
    if display and display != project.get("name"):
        repo.upsert_project(
            conn,
            name=display,
            framework=project.get("framework"),
            system=project.get("system") or {},
            created_at=project.get("created_at", ""),
        )

    dest_data = Path(root) / "data"
    dest_data.mkdir(parents=True, exist_ok=True)
    for csv in (src / "data").glob("*.csv"):
        shutil.copy2(csv, dest_data / csv.name)

    return counts


def reset_to_demo(conn: sqlite3.Connection, root: Path) -> tuple[int, int]:
    """Restore *root*'s engagement to a pristine Northwind demo.

    A user experimenting in the control plane can corrupt the engagement (delete a
    bound source, leave a half-edited control, accumulate stale extracts) until runs
    fail. This is the one-click recovery: it wipes the store and stale files, then
    reloads the demo via :func:`load_demo` — except it preserves the two *app
    settings* that are about the install, not the engagement: the AI selection
    (``system["ai"]``) and the launch update-check toggle
    (``system["check_updates_on_launch"]``).

    Unlike :func:`load_demo` (which only UPSERTs), this clears first, so leftover
    junk cannot survive the reset:

    1. Snapshot the preserved ``system`` keys from the current project.
    2. Wipe every user table — enumerated from ``sqlite_master`` so future
       migrations that add tables are covered automatically — with foreign-key
       enforcement off during the bulk delete.
    3. Clear stale files: empty ``root/data/`` (the demo re-copies its CSVs) and
       drop ``root/target/`` (stale workpapers/evidence). Missing dirs are ignored.
    4. Reload the demo with :func:`load_demo`.
    5. Restore the preserved ``system`` keys onto the reloaded demo project.

    The caller owns migration and the connection.

    Args:
        conn: An open, migrated connection to the target engagement store.
        root: The engagement directory whose store/files should be reset.

    Returns:
        ``(n_controls, n_sources)`` reloaded (from :func:`load_demo`).
    """
    # 1. Snapshot install-level settings that outlive the engagement.
    existing = repo.get_project(conn) or {}
    old_system = existing.get("system") or {}
    preserved = {k: old_system[k] for k in ("ai", "check_updates_on_launch") if k in old_system}

    # 2. Wipe every user table. PRAGMA foreign_keys toggles only outside a
    #    transaction, so commit first; enumerating from sqlite_master keeps this
    #    correct as future migrations add tables.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    for table in tables:
        conn.execute(f'DELETE FROM "{table}"')
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    # 3. Clear stale files under root (be defensive about missing dirs).
    data_dir = Path(root) / "data"
    if data_dir.is_dir():
        for child in data_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    shutil.rmtree(Path(root) / "target", ignore_errors=True)

    # 4. Reload the pristine demo.
    counts = load_demo(conn, root)

    # 5. Restore the preserved settings onto the reloaded demo project.
    if preserved:
        demo = repo.get_project(conn) or {}
        system = dict(demo.get("system") or {})
        system.update(preserved)
        repo.upsert_project(
            conn,
            name=demo.get("name", "") or "",
            framework=demo.get("framework"),
            system=system,
            created_at=demo.get("created_at", "") or "",
        )

    return counts
