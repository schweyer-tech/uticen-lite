"""Control metadata models parsed from control.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FrameworkRefs:
    """Framework reference tags for a control (NIST, ISO, etc.)."""

    nist: list[str] = field(default_factory=list)
    extra: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class RiskRef:
    """A risk linked to a control."""

    name: str
    description: str = ""
    inherent_rating: str | None = None


@dataclass
class SourceBinding:
    """A data source bound to a control, as declared in control.yaml.

    ``to_data_source()`` maps to the app's ``data_sources`` shape (the subset
    the app cares about): ``{type, key_config, column_mappings}``.
    """

    id: str
    type: str
    config: dict[str, Any]
    key_config: dict[str, Any]
    column_mappings: list[dict[str, Any]]

    def to_data_source(self) -> dict[str, Any]:
        """Return the app ``data_sources`` shape (type + key_config + column_mappings only)."""
        return {
            "type": self.type,
            "key_config": dict(self.key_config),
            "column_mappings": [dict(cm) for cm in self.column_mappings],
        }


@dataclass
class ControlDef:
    """A parsed control definition from control.yaml.

    Plain data holder — no validation logic lives here (that belongs to the
    YAML parser in task 7).  Only behaviour: ``to_dict()`` for serialisation.
    """

    id: str
    title: str
    objective: str
    narrative: str
    framework_refs: FrameworkRefs
    risk: RiskRef | None
    sources: list[SourceBinding]
    test_path: str
    severity_policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON / API payloads."""
        return {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "narrative": self.narrative,
            "framework_refs": {
                "nist": list(self.framework_refs.nist),
                "extra": {k: list(v) for k, v in self.framework_refs.extra.items()},
            },
            "risk": (
                {
                    "name": self.risk.name,
                    "description": self.risk.description,
                    "inherent_rating": self.risk.inherent_rating,
                }
                if self.risk is not None
                else None
            ),
            "sources": [s.to_data_source() for s in self.sources],
            "severity_policy": dict(self.severity_policy),
            "test_path": self.test_path,
        }
