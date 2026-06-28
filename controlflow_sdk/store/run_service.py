"""Store-backed control runner: load → execute → persist → render."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from controlflow_sdk.adapters.files import source_for
from controlflow_sdk.model.control import ControlDef, SourceBinding, Threshold
from controlflow_sdk.model.run import RunRecord
from controlflow_sdk.model.violation import Violation
from controlflow_sdk.model.workpaper import ProcedureSpec, Workpaper
from controlflow_sdk.pipeline.compile import (
    _subpipeline_for,
    compile_pipeline,
    compile_pipeline_procedures,
)
from controlflow_sdk.pipeline.materialize import materialize_steps
from controlflow_sdk.pipeline.model import Node, Pipeline, parse_pipeline
from controlflow_sdk.pipeline.procedures import effective_procedures, tests_for_procedure
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
    return Threshold.from_raw({
        "failure_threshold_pct": pct,
        "failure_threshold_count": count,
    })


def _severity_rank(sev: Any) -> int:
    """Order severities low < medium < high < critical (mirrors the renderer)."""
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(
        getattr(sev, "value", str(sev)), 1
    )


def _load_run_frames(
    root: Path, sources: dict[str, SourceBinding], pipeline: Pipeline
) -> dict[str, Any]:
    """``{source_id: DataFrame}`` for the pipeline's Import sources, via the source adapter.

    Loads each bound source exactly the way ``run_control`` does (``source_for(...).load()``)
    so the materialised frames — and the distinct-examined populations derived from them —
    match the run.  A missing/unknown source raises here and the caller degrades (0013).
    """
    frames: dict[str, Any] = {}
    for sid in pipeline.import_source_ids():
        frames[sid] = source_for(sources[sid], root).load().df
    return frames


def _distinct_examined(node_frames: dict[str, Any], tests: list[Node]) -> int | None:
    """``|⋃ distinct item-keys across each test's *input* (evaluated) frame|``.

    Each Test's input frame is its post-filter evaluated population (the rows the check
    actually examined); the union by item-key is the procedure's distinct-items-examined
    count.  Returns ``None`` when frames are unavailable (degrade to the run's population);
    falls back to the frame index when a test has no ``item_key_column``.
    """
    if not node_frames:
        return None
    seen: set[str] = set()
    for t in tests:
        if not t.inputs:
            continue
        frame = node_frames.get(t.inputs[0])
        if frame is None:
            return None
        key_col = t.config.get("item_key_column")
        if key_col and key_col in getattr(frame, "columns", []):
            seen.update(str(v) for v in frame[key_col].tolist())
        else:
            seen.update(str(i) for i in frame.index.tolist())
    return len(seen)


def _merge_violations(per_check: list[tuple[str, list[Violation]]]) -> list[Violation]:
    """Collapse per-check violations into one :class:`Violation` per item-key.

    ``details['checks']`` lists (sorted) the check labels that flagged the item; the
    surviving severity/description are the max-severity check's.  Sanitised JSON-native
    via :meth:`Violation.from_raw` (learning 0020).  Re-merging already-merged violations
    is lossless — any ``details['checks']`` already present is carried forward — so the
    control-level aggregate keeps every contributing check label.
    """
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for label, vlist in per_check:
        for v in vlist:
            slot = by_key.get(v.item_key)
            if slot is None:
                slot = {
                    "item_key": v.item_key,
                    "description": v.description,
                    "severity": v.severity,
                    "details": dict(v.details),
                    "_checks": [],
                    "_sev_rank": _severity_rank(v.severity),
                }
                by_key[v.item_key] = slot
                order.append(v.item_key)
            slot["details"].update(v.details)
            for existing in v.details.get("checks", []):  # carry forward on re-merge
                if existing and existing not in slot["_checks"]:
                    slot["_checks"].append(existing)
            if label and label not in slot["_checks"]:
                slot["_checks"].append(label)
            if _severity_rank(v.severity) > slot["_sev_rank"]:
                slot["_sev_rank"] = _severity_rank(v.severity)
                slot["severity"] = v.severity
                slot["description"] = v.description
    merged: list[Violation] = []
    for k in order:
        slot = by_key[k]
        details = dict(slot["details"])
        details["checks"] = sorted(slot["_checks"])
        merged.append(Violation.from_raw({
            "item_key": slot["item_key"],
            "description": slot["description"],
            "severity": slot["severity"],
            "details": details,
        }))
    return merged


def _run_multi_procedure(
    conn: sqlite3.Connection,
    root: Path,
    control: ControlDef,
    sources: dict,
    raw_pipeline: dict,
    executed_at: str,
) -> RunRecord:
    """Fan out a pipeline control through its procedures, assemble a workpaper.

    Uniform across ALL pipeline controls (single- and multi-test). For each effective
    procedure:
    1. Run EACH of its Test nodes separately (a single-terminal sub-pipeline compiled to
       the existing rule/python artifact), so we know which check flagged each item.
    2. Merge the per-check violations by item-key — one exception per item, annotated with
       the (sorted) labels of the checks that flagged it (``details['checks']``).
    3. Report a *distinct-items-examined* population: the union, by item-key, of each
       check's evaluated (post-filter) input frame.  Best-effort: degrades to the run's
       own population when frames are unavailable (0013).

    Persists one ``RunRecord`` per procedure (tagged ``procedure_id``) plus a control-level
    aggregate run (``procedure_id=""``) that callers receive for back-compat.  Assembles via
    ``Workpaper.assemble_procedures`` and writes the workpaper + aggregate evidence file.
    """
    pipeline = parse_pipeline(raw_pipeline)

    # Materialise node frames ONCE (full population) for distinct-examined populations.
    # Best-effort: degrade to {} (→ each run's own population) if a source is missing (0013).
    node_frames: dict[str, Any] = {}
    try:
        node_frames = materialize_steps(pipeline, _load_run_frames(root, sources, pipeline))
    except Exception:  # noqa: BLE001 — population is best-effort; never block the run
        node_frames = {}

    # The union compile of each procedure's checks → the workpaper's displayed test_code.
    union_by_pid = {cp.procedure_id: cp for cp in compile_pipeline_procedures(pipeline)}

    # Collect data samples once (deduped by source id).
    samples = collect_data_samples(control, sources, root)

    per_proc_runs: list[tuple[ProcedureSpec, RunRecord]] = []
    for proc in effective_procedures(pipeline):
        tests = tests_for_procedure(pipeline, proc.id)
        if not tests:
            continue

        # Run each check separately so we know which check flagged each item.
        per_check: list[tuple[str, list[Violation]]] = []
        last_run: RunRecord | None = None
        for t in tests:
            compiled = compile_pipeline(_subpipeline_for(pipeline, t))
            transient = ControlDef(
                id=control.id,
                title=proc.name or control.title,
                objective=control.objective,
                narrative=proc.narrative,
                framework_refs=control.framework_refs,
                risk=control.risk,
                sources=control.sources,
                test_path="",
                test_code=compiled.test_code if compiled.test_kind == "python" else None,
                rule_spec=compiled.rule_spec if compiled.test_kind == "rule" else None,
                threshold=control.threshold,
            )
            r = run_control(transient, sources, root, executed_at)
            label = t.title or t.config.get("title") or t.id
            per_check.append((str(label), list(r.violations)))
            last_run = r

        merged = _merge_violations(per_check)
        population = _distinct_examined(node_frames, tests)
        if population is None:
            population = last_run.population_size if last_run else 0

        proc_run = RunRecord(
            control_id=control.id,
            executed_at=executed_at,
            population_size=population,
            violations=merged,
            provenance=last_run.provenance if last_run else [],
        )
        proc_run.procedure_id = proc.id
        # Re-derive run_id to incorporate procedure_id; prevents collision when two
        # procedures share the same source snapshot (identical prov hashes → same base id).
        object.__setattr__(proc_run, "run_id", _procedure_run_id(proc_run, proc.id))
        repo.insert_run(conn, proc_run)

        # Displayed test_code: the union compile of all the procedure's checks (rule→text
        # or the generated union Python), resolved the same way as the single-control path.
        union_cp = union_by_pid.get(proc.id)
        if union_cp is not None and union_cp.result.test_kind == "python":
            display_code = union_cp.result.test_code or ""
        else:
            display_code = resolve_test_code(ControlDef(
                id=control.id,
                title=proc.name or control.title,
                objective=control.objective,
                narrative=proc.narrative,
                framework_refs=control.framework_refs,
                risk=control.risk,
                sources=control.sources,
                test_path="",
                test_code=(union_cp.result.test_code if union_cp else None),
                rule_spec=(union_cp.result.rule_spec if union_cp else None),
                threshold=control.threshold,
            ))

        proc_threshold = _per_procedure_threshold(
            {"failure_threshold_pct": proc.failure_threshold_pct,
             "failure_threshold_count": proc.failure_threshold_count},
            control.threshold,
        )
        per_proc_runs.append((
            ProcedureSpec(
                code=proc.code,
                title=proc.name or display_code,
                assertion=proc.assertion,
                narrative=proc.narrative,
                test_code=display_code,
                threshold=proc_threshold,
            ),
            proc_run,
        ))

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

    # Evidence: union of all procedures' violations (lossless re-merge keeps each
    # item's contributing check labels).
    all_violations = _merge_violations(
        [("", [v for _, run in per_proc_runs for v in run.violations])]
    )
    (ev_dir / f"{control.id}-violations.json").write_text(
        json.dumps([v.to_dict() for v in all_violations], indent=2),
        encoding="utf-8",
    )

    if not per_proc_runs:
        # Defensive: a pipeline with no runnable procedure — degrade, never 500 (0013).
        empty = RunRecord(control_id=control.id, executed_at=executed_at,
                          population_size=0, violations=[], provenance=[])
        repo.insert_run(conn, empty)
        return empty

    # Single-procedure pipeline → exactly ONE persisted run (the procedure run itself,
    # tagged with its procedure_id); no redundant ``procedure_id=""`` aggregate. A
    # multi-procedure control additionally persists a control-level aggregate run carrying
    # the distinct-examined population across ALL checks of ALL procedures + merged violations.
    union_run = per_proc_runs[0][1]
    if len(per_proc_runs) > 1:
        all_tests = [t for proc in effective_procedures(pipeline)
                     for t in tests_for_procedure(pipeline, proc.id)]
        agg_population = _distinct_examined(node_frames, all_tests)
        union_run = RunRecord(
            control_id=control.id,
            executed_at=executed_at,
            population_size=(agg_population if agg_population is not None
                             else per_proc_runs[0][1].population_size),
            violations=all_violations,
            provenance=per_proc_runs[0][1].provenance,
        )
        union_run.procedure_id = ""
        # Persist the aggregate so the run view can look it up by run_id.
        repo.insert_run(conn, union_run)
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

    For pipeline controls (any number of terminals): fans out through the
    control's procedures — running each procedure's checks, merging violations by
    item-key, and reporting a distinct-items-examined population — and assembles a
    multi-procedure workpaper.  Returns a control-level aggregate :class:`RunRecord`
    for back-compat.
    """
    project = load_project_from_store(conn)
    control = next((c for c in project.controls if c.id == control_id), None)
    if control is None:
        raise KeyError(f"no control {control_id!r} in store")

    # Any pipeline control fans out through its procedures (uniform for single- and
    # multi-test): per-check runs, merge-by-item-key, distinct-items-examined population.
    raw_ctrl = repo.get_control(conn, control_id)
    raw_pipeline = raw_ctrl.get("pipeline") if raw_ctrl else None
    if raw_pipeline:
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
