"""Shared bundle-build logic used by both ``cflow build`` and the web export route.

``build_bundle`` loads the project from the store, reconstructs run dicts via
:func:`_to_run_dicts`, assembles the manifest, and writes the zip.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from controlflow_sdk.bundle.archive import write_bundle
from controlflow_sdk.bundle.assemble import assemble_bundle
from controlflow_sdk.store import repo
from controlflow_sdk.store.loader import load_project_from_store


def _to_run_dicts(conn: sqlite3.Connection, controls: list) -> dict[str, list[dict]]:
    """Reconstruct ``RunRecord.to_dict()`` shapes from stored run dicts.

    The store returns raw dicts; this helper re-instantiates
    :class:`~controlflow_sdk.model.run.RunRecord` objects (with their derived
    properties) so the bundle receives exactly the same shape as the old
    ``run-log.json`` path produced.
    """
    from controlflow_sdk.model.run import RunRecord, SourceProvenance
    from controlflow_sdk.model.violation import Violation

    out: dict[str, list[dict]] = {}
    for c in controls:
        runs = repo.list_runs_for(conn, c.id)
        if not runs:
            continue
        rebuilt = []
        for r in runs:
            rr = RunRecord(
                control_id=r["control_id"],
                executed_at=r["executed_at"],
                population_size=r["population_size"],
                violations=[Violation.from_raw(v) for v in r["violations"]],
                provenance=[SourceProvenance(**p) for p in r["provenance"]],
            )
            rebuilt.append(rr.to_dict())
        out[c.id] = rebuilt
    return out


def build_bundle(
    conn: sqlite3.Connection,
    root: Path,
    out_path: Path,
    generated_at: str,
) -> Path:
    """Load the project, assemble a manifest, and write a bundle zip.

    Args:
        conn: Open SQLite connection to the engagement store.
        root: Project root directory (contains ``target/``).
        out_path: Destination path for the bundle zip.
        generated_at: ISO-8601 timestamp to embed in the manifest.

    Returns:
        The path of the written zip file.

    Raises:
        ValueError: When there are no runs to export.
    """
    project = load_project_from_store(conn)
    runs_by_control = _to_run_dicts(conn, project.controls)
    if not runs_by_control:
        raise ValueError("no runs to export")
    manifest = assemble_bundle(project, runs_by_control, generated_at)
    target_dir = root / "target"
    return write_bundle(manifest, target_dir, out_path)
