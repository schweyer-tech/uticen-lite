"""Project-level YAML loaders for cflow.yaml and sources.yaml.

Parses project configuration and data source definitions, validates them
against the bundled JSON schemas, and returns typed dataclass instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from uticen_lite.model.control import SourceBinding
from uticen_lite.schema.validate import validate_sources


class ProjectError(Exception):
    """Raised when a project file fails schema validation.

    The message aggregates all schema error strings so callers get a full
    picture in one exception.
    """


@dataclass
class ProjectConfig:
    """Parsed representation of ``cflow.yaml``.

    Attributes:
        name:      Human-readable project name.
        framework: Optional compliance framework label (e.g. "NIST SP 800-53").
        system:    Arbitrary system-metadata dict from the ``system:`` key.
        defaults:  Default settings dict from the ``defaults:`` key.
    """

    name: str
    framework: str | None
    system: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)


def load_project_config(root: Path) -> ProjectConfig:
    """Parse ``<root>/cflow.yaml`` and return a :class:`ProjectConfig`.

    Args:
        root: Path to the project root directory.

    Returns:
        A :class:`ProjectConfig` with the parsed values.

    Raises:
        FileNotFoundError: If ``cflow.yaml`` is not present under *root*.
    """
    path = root / "cflow.yaml"
    if not path.exists():
        raise FileNotFoundError(f"cflow.yaml not found in {root}")

    with path.open(encoding="utf-8") as fh:
        doc: dict[str, Any] = yaml.safe_load(fh) or {}

    return ProjectConfig(
        name=doc.get("name", ""),
        framework=doc.get("framework"),
        system=doc.get("system") or {},
        defaults=doc.get("defaults") or {},
    )


def load_sources(root: Path) -> dict[str, SourceBinding]:
    """Parse ``<root>/sources.yaml``, validate against the sources schema,
    and return a mapping of source ``id`` → :class:`SourceBinding`.

    Args:
        root: Path to the project root directory.

    Returns:
        Dict keyed by source ``id`` containing :class:`SourceBinding` instances.

    Raises:
        FileNotFoundError: If ``sources.yaml`` is not present under *root*.
        ProjectError: If the file fails schema validation (all errors aggregated).
    """
    path = root / "sources.yaml"
    if not path.exists():
        raise FileNotFoundError(f"sources.yaml not found in {root}")

    with path.open(encoding="utf-8") as fh:
        doc: dict[str, Any] = yaml.safe_load(fh) or {}

    errors = validate_sources(doc)
    if errors:
        msg = "sources.yaml failed schema validation:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ProjectError(msg)

    result: dict[str, SourceBinding] = {}
    for src in doc.get("sources", []):
        binding = SourceBinding(
            id=src["id"],
            type=src["type"],
            config=dict(src.get("config") or {}),
            key_config=dict(src.get("key_config") or {}),
            column_mappings=[dict(cm) for cm in src.get("column_mappings") or []],
            description=src.get("description"),
            completeness_accuracy=src.get("completeness_accuracy"),
            extract_date=src.get("extract_date"),
            title=src.get("title"),
        )
        result[binding.id] = binding

    return result
