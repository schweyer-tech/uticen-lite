"""Shared engagement-import logic reused by ``cflow import`` and the control plane.

The CLI (`controlflow_sdk.cli.import_cmd`) and the web first-run flow
(`controlflow_sdk.plane.routes.setup`) both turn a YAML project directory into rows
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

from controlflow_sdk.model.control import SourceBinding
from controlflow_sdk.pipeline.compile import compile_pipeline
from controlflow_sdk.pipeline.model import parse_pipeline
from controlflow_sdk.project.discovery import Project
from controlflow_sdk.store import repo


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
    from controlflow_sdk.adapters.files import source_for

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
            conn, source_id=sid, stored_path=_path,
            original_name=Path(_path).name, as_of_date=binding.extract_date,
            row_count=_row_count_of(binding, src), uploaded_at=_import_stamp(),
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
    touch ``bundle.schema.json`` — and ``cflow``/the web runner reuse the unchanged
    rule/python execution paths against whatever lands in ``rule_spec``/``test_code``.
    """
    test_path = getattr(control, "test_path", "") or ""
    control_dir = Path(test_path).parent if test_path else None

    if control_dir is not None:
        rule_file = control_dir / "rule.yaml"
        if rule_file.is_file():
            rule_spec = yaml.safe_load(rule_file.read_text(encoding="utf-8")) or {}
            return {"test_kind": "rule", "rule_spec": rule_spec,
                    "test_code": None, "pipeline": None}

        pipeline_file = control_dir / "pipeline.yaml"
        if pipeline_file.is_file():
            graph = yaml.safe_load(pipeline_file.read_text(encoding="utf-8")) or {}
            pipeline = parse_pipeline(graph)  # validate the graph eagerly
            compiled = compile_pipeline(pipeline)
            return {"test_kind": "pipeline", "rule_spec": compiled.rule_spec,
                    "test_code": compiled.test_code, "pipeline": graph}

    code = Path(test_path).read_text(encoding="utf-8") if test_path else ""
    return {"test_kind": "python", "rule_spec": None,
            "test_code": code, "pipeline": None}


def demo_source_dir() -> Path:
    """Locate the bundled Northwind demo engagement.

    Resolves the packaged copy first (``controlflow_sdk/_demo/northwind-trading``,
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
        "Reinstall controlflow-sdk or run from a source checkout."
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
