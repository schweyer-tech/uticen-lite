"""Shared bundle-build logic used by both ``cflow build`` and the web export route.

``build_bundle`` loads the project from the store, reconstructs run dicts via
:func:`_to_run_dicts`, assembles the manifest, and writes the zip.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from controlflow_sdk.bundle.archive import write_bundle
from controlflow_sdk.bundle.assemble import assemble_bundle
from controlflow_sdk.pipeline.lint import LintError, lint_pipeline
from controlflow_sdk.pipeline.model import parse_pipeline
from controlflow_sdk.store import repo
from controlflow_sdk.store.loader import load_project_from_store


def _enforce_custom_python_gate(conn: sqlite3.Connection) -> None:
    """HARD export gate (§8 layer 3): refuse the bundle on a tripping custom node.

    Re-run the same allowlist AST deny-scan used at save over every stored
    ``test_kind='pipeline'`` control's graph. A bundle is the contract surface
    consumed by the ControlFlow app, so the canvas's provenance claim ("custom
    nodes never read a source") must hold where it's *consumed*, not only where
    it's typed — same posture as ``tests/test_contract_export.py``. Decision:
    hard BLOCK (raise), not a warning. The message names the offending control +
    node and points at the "Convert to Python test" offramp.
    """
    blocking: list[str] = []
    for c in repo.list_controls(conn):
        if c.get("test_kind") != "pipeline":
            continue
        graph = c.get("pipeline")
        if not graph:
            continue
        try:
            errors = lint_pipeline(parse_pipeline(graph))
        except ValueError as exc:  # malformed stored graph → also block the bundle
            errors = [f"pipeline failed to parse: {exc}"]
        for err in errors:
            blocking.append(f"control {c['id']!r}: {err}")
    if blocking:
        raise LintError(blocking)


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
        # list_runs_for returns DESC (newest-first); reverse to ASC so that
        # runs[-1] in assemble_bundle correctly selects the latest run.
        runs = list(reversed(repo.list_runs_for(conn, c.id)))
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
        LintError: When a ``pipeline`` control has a Custom Python node that
            trips the §8 allowlist deny-scan (hard export gate).
    """
    # §8 layer 3: hard export gate — refuse the bundle BEFORE assembling if any
    # stored pipeline's custom node could read a file / reach outside `rows`.
    _enforce_custom_python_gate(conn)
    project = load_project_from_store(conn)
    runs_by_control = _to_run_dicts(conn, project.controls)
    if not runs_by_control:
        raise ValueError("no runs to export")
    manifest = assemble_bundle(project, runs_by_control, generated_at)
    target_dir = root / "target"
    return write_bundle(manifest, target_dir, out_path)
