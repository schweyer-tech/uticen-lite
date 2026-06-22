"""Store-backed control runner: load → execute → persist → render."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from controlflow_sdk.model.control import ControlDef, Threshold
from controlflow_sdk.model.run import RunRecord
from controlflow_sdk.model.workpaper import ProcedureSpec, Workpaper
from controlflow_sdk.pipeline.compile import compile_pipeline_procedures
from controlflow_sdk.pipeline.model import parse_pipeline
from controlflow_sdk.render.html import render_html
from controlflow_sdk.render.markdown import render_markdown
from controlflow_sdk.rules.resolve import resolve_test_code
from controlflow_sdk.runner.execute import collect_data_samples, run_control
from controlflow_sdk.store import repo
from controlflow_sdk.store.loader import load_project_from_store


def _procedure_run_id(run: RunRecord, procedure_id: str) -> str:
    """Derive a stable run_id that incorporates *procedure_id*.

    Extends the base run_id derivation (control_id + executed_at + prov hashes)
    with the procedure_id so two procedures sharing the same source snapshot
    receive distinct run_ids and don't collide in ``INSERT OR REPLACE``.
    """
    prov_hashes = "".join(p.sha256 for p in run.provenance)
    raw = run.control_id + run.executed_at + prov_hashes + procedure_id
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _per_procedure_threshold(
    pipeline_node_config: dict,
    control_threshold: Threshold,
) -> Threshold:
    """Derive the per-procedure threshold from a terminal node's config.

    Reads ``failure_threshold_pct`` and ``failure_threshold_count`` from the
    terminal node's config dict; falls back to the control-level threshold when
    neither is set.
    """
    pct = pipeline_node_config.get("failure_threshold_pct")
    count = pipeline_node_config.get("failure_threshold_count")
    if pct is None and count is None:
        return control_threshold
    return Threshold(
        failure_threshold_pct=float(pct) if pct is not None else None,
        failure_threshold_count=int(count) if count is not None else None,
    )


def _run_multi_procedure(
    conn: sqlite3.Connection,
    root: Path,
    control: ControlDef,
    sources: dict,
    raw_pipeline: dict,
    executed_at: str,
) -> RunRecord:
    """Fan out: one run per terminal procedure, assemble a multi-procedure workpaper.

    For each compiled procedure:
    1. Build a transient ControlDef carrying ONLY that procedure's compiled artifact.
    2. Run via the existing ``run_control`` (uses the same loaded sources).
    3. Tag ``RunRecord.procedure_id = proc.procedure_id``; persist via ``insert_run``.

    Assembles via ``Workpaper.assemble_procedures`` and writes one workpaper + union
    evidence file.  Returns a union aggregate RunRecord for back-compat with callers
    that expect a single record (violations = concatenation of all procedures').
    """
    pipeline = parse_pipeline(raw_pipeline)
    procedures = compile_pipeline_procedures(pipeline)

    # Collect data samples once (deduped by source id).
    samples = collect_data_samples(control, sources, root)

    per_proc_runs: list[tuple[ProcedureSpec, RunRecord]] = []
    for proc in procedures:
        # Build a transient ControlDef with ONLY this procedure's compiled artifact.
        transient = ControlDef(
            id=control.id,
            title=proc.title,
            objective=control.objective,
            narrative=proc.narrative,
            framework_refs=control.framework_refs,
            risk=control.risk,
            sources=control.sources,
            test_path="",
            test_code=proc.result.test_code if proc.result.test_kind == "python" else None,
            rule_spec=proc.result.rule_spec if proc.result.test_kind == "rule" else None,
            threshold=control.threshold,
        )

        run = run_control(transient, sources, root, executed_at)
        run.procedure_id = proc.procedure_id
        # Re-derive run_id to incorporate procedure_id; prevents collision when two
        # procedures share the same source snapshot (identical prov hashes → same base id).
        object.__setattr__(run, "run_id", _procedure_run_id(run, proc.procedure_id))
        repo.insert_run(conn, run)

        # Per-procedure threshold: from the terminal node's config, else control threshold.
        terminal_node = pipeline.node(proc.procedure_id)
        proc_threshold = _per_procedure_threshold(terminal_node.config, control.threshold)

        # Reuse the same resolver as the single-procedure path: transient already
        # carries exactly one of rule_spec/test_code from the compiled artifact.
        resolved_code = resolve_test_code(transient)
        spec = ProcedureSpec(
            title=proc.title,
            narrative=proc.narrative,
            test_code=resolved_code,
            threshold=proc_threshold,
        )
        per_proc_runs.append((spec, run))

    wp = Workpaper.assemble_procedures(
        control,
        per_proc_runs,
        generated_at=executed_at,
        data_samples=samples,
    )

    wp_dir = root / "target" / "workpapers"
    ev_dir = root / "target" / "evidence"
    wp_dir.mkdir(parents=True, exist_ok=True)
    ev_dir.mkdir(parents=True, exist_ok=True)

    (wp_dir / f"{control.id}.html").write_text(render_html(wp), encoding="utf-8")
    (wp_dir / f"{control.id}.md").write_text(render_markdown(wp), encoding="utf-8")

    # Evidence: union of all procedures' violations.
    all_violations = [v for _, run in per_proc_runs for v in run.violations]
    (ev_dir / f"{control.id}-violations.json").write_text(
        json.dumps([v.to_dict() for v in all_violations], indent=2),
        encoding="utf-8",
    )

    # Union aggregate record for back-compat: concatenate violations, use trunk population size.
    # population_size is identical across all procedures (they share the same primary source).
    union_run = per_proc_runs[0][1]
    if len(per_proc_runs) > 1:
        from controlflow_sdk.model.run import RunRecord as RR

        union_run = RR(
            control_id=control.id,
            executed_at=executed_at,
            population_size=per_proc_runs[0][1].population_size,
            violations=all_violations,
            provenance=per_proc_runs[0][1].provenance,
            procedure_id="",
        )
    return union_run


def run_control_in_store(
    conn: sqlite3.Connection,
    root: Path,
    control_id: str,
    executed_at: str,
) -> RunRecord:
    """Load a control from the store, run it, persist and render the workpaper.

    Steps:
    1. Load the :class:`~controlflow_sdk.project.discovery.Project` from *conn*.
    2. Locate the control by *control_id*.
    3. Execute the control via :func:`~controlflow_sdk.runner.execute.run_control`.
    4. Persist the :class:`~controlflow_sdk.model.run.RunRecord` via
       :func:`~controlflow_sdk.store.repo.insert_run`.
    5. Collect data samples and assemble a
       :class:`~controlflow_sdk.model.workpaper.Workpaper`.
    6. Write ``target/workpapers/<id>.html``, ``target/workpapers/<id>.md``, and
       ``target/evidence/<id>-violations.json`` under *root*.
    7. Return the :class:`~controlflow_sdk.model.run.RunRecord`.

    For rule controls (``test_kind == "rule"``) the rendered rule text is used as
    the procedure's ``test_code`` so the workpaper shows what logic was evaluated.

    For pipeline controls with ≥2 terminals: fans out — one run per terminal
    procedure — and assembles a multi-procedure workpaper.  Returns a union
    aggregate :class:`RunRecord` for back-compat.
    """
    project = load_project_from_store(conn)
    control = next((c for c in project.controls if c.id == control_id), None)
    if control is None:
        raise KeyError(f"no control {control_id!r} in store")

    # Check for a multi-terminal pipeline: read the raw pipeline from the store.
    raw_ctrl = repo.get_control(conn, control_id)
    raw_pipeline = raw_ctrl.get("pipeline") if raw_ctrl else None
    if raw_pipeline:
        pipeline = parse_pipeline(raw_pipeline)
        if len(pipeline.terminals) >= 2:
            return _run_multi_procedure(
                conn, root, control, project.sources, raw_pipeline, executed_at
            )

    # ── Single-procedure path (unchanged) ─────────────────────────────────────
    run = run_control(control, project.sources, root, executed_at)
    repo.insert_run(conn, run)

    samples = collect_data_samples(control, project.sources, root)

    # Resolve the test code shown in the workpaper.  Inline-python controls
    # already carry test_code; rule controls have no .py file so we render the
    # rule to readable text.  File-based controls (test_code is None) keep None
    # here so Workpaper.assemble defers the disk read to assemble time.
    resolved_test_code: str | None
    if control.test_code is not None:
        resolved_test_code = control.test_code
    elif control.rule_spec is not None:
        resolved_test_code = resolve_test_code(control)  # renders rule → text
    else:
        resolved_test_code = None  # file-based — Workpaper.assemble reads test_path

    wp = Workpaper.assemble(
        control,
        run,
        generated_at=executed_at,
        data_samples=samples,
        test_code=resolved_test_code,
    )

    wp_dir = root / "target" / "workpapers"
    ev_dir = root / "target" / "evidence"
    wp_dir.mkdir(parents=True, exist_ok=True)
    ev_dir.mkdir(parents=True, exist_ok=True)

    (wp_dir / f"{control_id}.html").write_text(render_html(wp), encoding="utf-8")
    (wp_dir / f"{control_id}.md").write_text(render_markdown(wp), encoding="utf-8")
    (ev_dir / f"{control_id}-violations.json").write_text(
        json.dumps([v.to_dict() for v in run.violations], indent=2),
        encoding="utf-8",
    )

    return run
