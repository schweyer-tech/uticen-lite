"""Store-backed control runner: load → execute → persist → render."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from controlflow_sdk.model.run import RunRecord
from controlflow_sdk.model.workpaper import Workpaper
from controlflow_sdk.render.html import render_html
from controlflow_sdk.render.markdown import render_markdown
from controlflow_sdk.rules.render_rule import rule_to_text
from controlflow_sdk.rules.spec import parse_rule_spec
from controlflow_sdk.runner.execute import collect_data_samples, run_control
from controlflow_sdk.store import repo
from controlflow_sdk.store.loader import load_project_from_store


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
    """
    project = load_project_from_store(conn)
    control = next((c for c in project.controls if c.id == control_id), None)
    if control is None:
        raise KeyError(f"no control {control_id!r} in store")

    run = run_control(control, project.sources, root, executed_at)
    repo.insert_run(conn, run)

    samples = collect_data_samples(control, project.sources, root)

    # Resolve the test code shown in the workpaper.  Rule controls have no .py
    # file, so we render the rule spec to human-readable text instead.  For
    # inline-python controls we pass test_code directly so assemble skips the
    # disk read.  File-based controls pass None → assemble reads test_path.
    resolved_test_code: str | None
    if control.test_kind == "rule" and control.rule_spec is not None:
        resolved_test_code = rule_to_text(parse_rule_spec(control.rule_spec))
    else:
        resolved_test_code = control.test_code  # str | None — None → assemble reads test_path

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
