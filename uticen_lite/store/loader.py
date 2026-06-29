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


def _source_config(src: dict) -> dict:
    config = {"path": src["path"], "format": src["format"]}
    if src.get("sheet"):
        config["sheet"] = src["sheet"]
    return config


def _binding(src: dict) -> SourceBinding:
    return SourceBinding(
        id=src["id"],
        type="file",
        config=_source_config(src),
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
        title=src.get("title"),
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
                    rationale=c["failure_threshold_rationale"],
                ),
            )
        )
    return Project(config=config, sources=bindings, controls=controls)
