"""Shared bundle-build logic used by both ``uticen-lite build`` and the web export route.

``build_bundle`` loads the project from the store, reconstructs run dicts via
:func:`_to_run_dicts`, assembles the manifest, and writes the zip.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from uticen_lite.bundle.archive import write_bundle
from uticen_lite.bundle.assemble import assemble_bundle
from uticen_lite.pipeline.lint import LintError, lint_pipeline
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.store import repo
from uticen_lite.store.loader import load_project_from_store


def _enforce_custom_python_gate(conn: sqlite3.Connection) -> None:
    """HARD export gate (§8 layer 3): refuse the bundle on a tripping custom node.

    Re-run the same allowlist AST deny-scan used at save over every stored
    ``test_kind='pipeline'`` control's graph. A bundle is the contract surface
    consumed by the Uticen app, so the canvas's provenance claim ("custom
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


def _to_run_dicts(
    conn: sqlite3.Connection,
    controls: list,
) -> tuple[dict[str, list[dict]], dict[str, dict[str, dict]]]:
    """Reconstruct ``RunRecord.to_dict()`` shapes from stored run dicts.

    The store returns raw dicts; this helper re-instantiates
    :class:`~uticen_lite.model.run.RunRecord` objects (with their derived
    properties) so the bundle receives exactly the same shape as the old
    ``run-log.json`` path produced.

    Returns:
        A tuple of:
        - ``runs_by_control``: ``{control_id: [run_dict, ...]}`` in chronological
          (ASC) order, without ``procedure_id`` (not a bundle field).
        - ``procedure_run_map``: ``{control_id: {procedure_id: latest_run_dict}}``
          for controls that have per-procedure runs (``procedure_id != ""``).
          Run dicts here also omit ``procedure_id``.
    """
    from uticen_lite.model.run import RunRecord, SourceProvenance
    from uticen_lite.model.violation import Violation

    runs_by_control: dict[str, list[dict]] = {}
    procedure_run_map: dict[str, dict[str, dict]] = {}

    for c in controls:
        # list_runs_for returns DESC (newest-first); reverse to ASC so that
        # runs[-1] in assemble_bundle correctly selects the latest run.
        raw_runs = list(reversed(repo.list_runs_for(conn, c.id)))
        if not raw_runs:
            continue

        rebuilt: list[dict] = []
        # Track latest run per procedure_id (for multi-procedure controls).
        # Iterating ASC means we overwrite with progressively newer runs, so
        # after the loop proc_latest[pid] == the newest run for that procedure.
        proc_latest: dict[str, dict] = {}

        for r in raw_runs:
            rr = RunRecord(
                control_id=r["control_id"],
                executed_at=r["executed_at"],
                population_size=r["population_size"],
                violations=[Violation.from_raw(v) for v in r["violations"]],
                provenance=[SourceProvenance(**p) for p in r["provenance"]],
            )
            run_dict = rr.to_dict()
            rebuilt.append(run_dict)
            pid = r.get("procedure_id") or ""
            if pid:
                proc_latest[pid] = run_dict

        runs_by_control[c.id] = rebuilt
        if proc_latest:
            procedure_run_map[c.id] = proc_latest

    return runs_by_control, procedure_run_map


def _procedure_info_by_control(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Build per-procedure metadata for multi-terminal pipeline controls.

    For each stored control that is a ``pipeline`` with ≥2 terminals, compile
    the procedures and return ``{control_id: [{procedure_id, title, narrative,
    test_code}, ...]}``.  Single-terminal or non-pipeline controls are excluded
    (they use the existing single-procedure path in ``_build_workpaper``).

    ``test_code`` here is the per-procedure rendered text (rule → text or
    generated Python) derived from each terminal's sub-pipeline — the same
    source used by ``_run_multi_procedure`` when building the workpaper.
    """
    from uticen_lite.model.control import ControlDef, FrameworkRefs
    from uticen_lite.pipeline.compile import compile_pipeline_procedures
    from uticen_lite.rules.resolve import resolve_test_code

    out: dict[str, list[dict]] = {}
    for raw in repo.list_controls(conn):
        if raw.get("test_kind") != "pipeline":
            continue
        graph = raw.get("pipeline")
        if not graph:
            continue
        try:
            pipeline = parse_pipeline(graph)
        except ValueError:
            continue
        if len(pipeline.terminals) < 2:
            continue

        procs = compile_pipeline_procedures(pipeline)
        proc_info: list[dict] = []
        for proc in procs:
            # Build a transient ControlDef carrying only this procedure's artifact
            # so resolve_test_code renders rule→text the same way run_service does.
            transient = ControlDef(
                id=raw["id"],
                title=proc.title,
                objective=raw["objective"],
                narrative=proc.narrative,
                framework_refs=FrameworkRefs(),
                risk=None,
                sources=[],
                test_path="",
                test_code=proc.result.test_code if proc.result.test_kind == "python" else None,
                rule_spec=proc.result.rule_spec if proc.result.test_kind == "rule" else None,
            )
            proc_info.append({
                "procedure_id": proc.procedure_id,
                "code": proc.code,
                "assertion": proc.assertion,
                "title": proc.title,
                "narrative": proc.narrative,
                "test_code": resolve_test_code(transient),
            })
        out[raw["id"]] = proc_info
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
    runs_by_control, procedure_run_map = _to_run_dicts(conn, project.controls)
    if not runs_by_control:
        raise ValueError("no runs to export")
    proc_info_map = _procedure_info_by_control(conn)
    manifest = assemble_bundle(
        project, runs_by_control, generated_at,
        procedure_run_map=procedure_run_map,
        procedure_info_by_control=proc_info_map,
    )
    target_dir = root / "target"
    return write_bundle(manifest, target_dir, out_path)
