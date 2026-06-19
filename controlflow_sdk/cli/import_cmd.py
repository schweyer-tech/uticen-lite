"""``cflow import`` — load a YAML project into a local controlplane.db store."""

from __future__ import annotations

import argparse
from pathlib import Path

from controlflow_sdk.project.discovery import Project
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


def import_cmd(args: argparse.Namespace) -> int:
    """Import a YAML project directory into a controlplane.db engagement store.

    Args:
        args: Parsed CLI namespace with:
            ``src``  — path to the YAML project directory.
            ``into`` — target engagement directory (defaults to ``src``).

    Returns:
        0 on success.
    """
    src = Path(args.src)
    into = Path(args.into) if getattr(args, "into", None) else src
    project = Project.load(src)

    conn = connect(into)
    migrate(conn)

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

    for control in project.controls:
        code = Path(control.test_path).read_text(encoding="utf-8") if control.test_path else ""
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
            test_kind="python",
            test_code=code,
            failure_threshold_pct=control.threshold.failure_threshold_pct,
            failure_threshold_count=control.threshold.failure_threshold_count,
        )
        repo.set_control_sources(conn, control.id, [s.id for s in control.sources])

    print(f"IMPORT  {len(project.controls)} controls / {len(project.sources)} sources → {into}")
    return 0
